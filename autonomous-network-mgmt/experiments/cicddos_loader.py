"""
CICDDoS2019 CSV → SecurityAnomalyDetector 피처 변환 로더.

CICFlowMeter가 생성한 플로우 단위 CSV를 1초 단위 시간 윈도우로 집계하여
SecurityAnomalyDetector.update()/.detect() 가 기대하는 피처 벡터
[bandwidth, latency, packet_loss, syn_ratio, unique_src_count, pkt_rate] 로 변환한다.

알려진 한계:
  - bandwidth/latency/packet_loss는 CICDDoS2019에 대응 컬럼이 없음 →
    고정된 "정상" 기본값 사용 (시뮬레이션의 정상 트래픽 범위와 동일선상).
    즉 이 3개 차원은 IsolationForest 입력으로는 들어가지만 실제 검증 대상이 아니다.
  - syn_ratio/unique_src_count/pkt_rate 3개 차원만 실데이터로 진짜 검증된다.
"""
import os
from dataclasses import dataclass
from typing import Iterator, Optional

import pandas as pd

# CICDDoS2019 컬럼명은 배포 버전에 따라 공백/대소문자가 다를 수 있어 정규화한다.
_COLUMN_ALIASES = {
    "timestamp":         ["Timestamp", " Timestamp"],
    "source_ip":         ["Source IP", " Source IP", "SrcIP"],
    "syn_flag_count":    ["SYN Flag Count", " SYN Flag Count"],
    "total_fwd_packets": ["Total Fwd Packets", " Total Fwd Packets"],
    "total_bwd_packets": ["Total Backward Packets", " Total Backward Packets"],
    "label":             ["Label", " Label"],
}
_REQUIRED_KEYS = list(_COLUMN_ALIASES.keys())

# 실데이터에 없는 3개 피처의 placeholder (시뮬레이션 '정상' 구간과 동일 스케일)
PLACEHOLDER_BANDWIDTH   = 500.0   # Mbps, metric_generator 정상 범위 중앙값과 동일선상
PLACEHOLDER_LATENCY     = 10.0    # ms, SLA(50ms) 이내 정상값
PLACEHOLDER_PACKET_LOSS = 0.001   # SLA(0.01) 이내 정상값


@dataclass
class WindowResult:
    window_start: float   # 윈도우 시작 (epoch seconds // window_sec * window_sec)
    features:     dict    # update()/detect()에 **로 바로 전달 가능한 6피처 dict
    is_attack:    bool    # 윈도우 내 BENIGN이 아닌 플로우가 하나라도 있으면 True
    attack_label: str     # 윈도우 내 최다 빈도 레이블 ("BENIGN" 또는 공격명)


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {}
    for canon, aliases in _COLUMN_ALIASES.items():
        matched = next((a for a in aliases if a in df.columns), None)
        if matched is None:
            matched = next(
                (c for c in df.columns if c.strip().lower() == canon.replace("_", " ")),
                None,
            )
        if matched is not None:
            rename_map[matched] = canon
    df = df.rename(columns=rename_map)
    missing = [k for k in _REQUIRED_KEYS if k not in df.columns]
    if missing:
        raise ValueError(
            f"CICDDoS2019 CSV에 필요한 컬럼이 없습니다: {missing} "
            f"(실제 컬럼 일부: {list(df.columns)[:10]})"
        )
    return df[_REQUIRED_KEYS].copy()


def _to_epoch_seconds(series: pd.Series) -> pd.Series:
    # pandas 버전에 따라 datetime64 단위(ns/us/ms)가 달라 astype("int64")는
    # 단위가 일정하지 않다 — Timestamp 빼기로 단위 독립적인 epoch초를 계산한다.
    ts = pd.to_datetime(series, errors="coerce")
    return (ts - pd.Timestamp("1970-01-01")) / pd.Timedelta(seconds=1)


def _aggregate_window(window_id: int, sub: pd.DataFrame, window_sec: float) -> WindowResult:
    total_fwd     = sub["total_fwd_packets"].sum()
    total_bwd     = sub["total_bwd_packets"].sum()
    total_packets = total_fwd + total_bwd
    syn_count     = sub["syn_flag_count"].sum()

    pkt_rate  = total_packets / window_sec
    syn_ratio = syn_count / max(total_packets, 1)
    unique_src = sub["source_ip"].nunique()

    labels = sub["label"].astype(str)
    non_benign = labels[labels != "BENIGN"]
    is_attack = not non_benign.empty
    attack_label = non_benign.mode().iloc[0] if is_attack else "BENIGN"

    features = {
        "bandwidth":        PLACEHOLDER_BANDWIDTH,
        "latency":          PLACEHOLDER_LATENCY,
        "packet_loss":      PLACEHOLDER_PACKET_LOSS,
        "syn_ratio":        float(syn_ratio),
        "unique_src_count": float(unique_src),
        "pkt_rate":         float(pkt_rate),
    }
    return WindowResult(
        window_start=float(window_id) * window_sec,
        features=features,
        is_attack=is_attack,
        attack_label=attack_label,
    )


class CICDDoSWindowIterator:
    """CSV를 chunksize 단위로 스트리밍 읽으며 1초 윈도우로 집계한다.

    청크 경계를 넘는 윈도우를 올바르게 합치기 위해, 각 청크의 마지막
    (아직 완성되지 않았을 수 있는) 윈도우는 carry-over로 보관해 다음 청크와 합산한다.
    """

    def __init__(self, csv_path: str, window_sec: float = 1.0, chunksize: int = 200_000):
        self.csv_path   = csv_path
        self.window_sec = window_sec
        self.chunksize  = chunksize

    def windows(self) -> Iterator[WindowResult]:
        carry: Optional[pd.DataFrame] = None

        for chunk in pd.read_csv(self.csv_path, chunksize=self.chunksize, low_memory=False):
            chunk = _normalize_columns(chunk)
            chunk["_epoch"] = _to_epoch_seconds(chunk["timestamp"])
            chunk = chunk.dropna(subset=["_epoch"])
            if chunk.empty:
                continue
            chunk["_window_id"] = (chunk["_epoch"] // self.window_sec).astype("int64")

            if carry is not None:
                chunk = pd.concat([carry, chunk], ignore_index=True)

            window_ids = sorted(chunk["_window_id"].unique())
            last_id = window_ids[-1]

            for wid in window_ids[:-1]:
                sub = chunk[chunk["_window_id"] == wid]
                yield _aggregate_window(wid, sub, self.window_sec)

            carry = chunk[chunk["_window_id"] == last_id]

        if carry is not None and not carry.empty:
            wid = carry["_window_id"].iloc[0]
            yield _aggregate_window(wid, carry, self.window_sec)


def load_windows(csv_path: str, window_sec: float = 1.0) -> list[WindowResult]:
    """편의 함수: 전체 CSV를 윈도우 리스트로 변환 (권장 단일 공격파일 크기 기준 메모리에 적재 가능)."""
    return list(CICDDoSWindowIterator(csv_path, window_sec).windows())


if __name__ == "__main__":
    # 실데이터 도착 전 로더 집계 로직(특히 청크 경계 carry-over)을 점검하는 자가 테스트.
    csv_text = (
        "Timestamp,Source IP,SYN Flag Count,Total Fwd Packets,Total Backward Packets,Label\n"
        "2019-01-12 13:00:00.100000,10.0.0.1,1,5,3,BENIGN\n"
        "2019-01-12 13:00:00.200000,10.0.0.2,0,2,2,BENIGN\n"
        "2019-01-12 13:00:01.100000,10.0.0.3,10,10,0,Syn\n"
        "2019-01-12 13:00:01.300000,10.0.0.4,8,8,0,Syn\n"
        "2019-01-12 13:00:02.100000,10.0.0.5,1,4,4,BENIGN\n"
    )
    tmp_path = "_cicddos_loader_selftest.csv"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(csv_text)
    try:
        windows = load_windows(tmp_path, window_sec=1.0)
        assert len(windows) == 3, f"기대 윈도우 3개, 실제 {len(windows)}"
        assert windows[0].is_attack is False
        assert windows[1].is_attack is True and windows[1].attack_label == "Syn"
        assert windows[1].features["unique_src_count"] == 2.0
        assert windows[2].is_attack is False
        print("OK — self-test 통과")
        for w in windows:
            print(f"  t={w.window_start:.0f} attack={w.is_attack} label={w.attack_label} features={w.features}")
    finally:
        os.remove(tmp_path)
