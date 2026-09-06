"""
CICDDoS2019 CSV → SecurityAnomalyDetector 피처 변환 로더.

CICFlowMeter가 생성한 플로우 단위 CSV를 1초 단위 시간 윈도우로 집계하여
SecurityAnomalyDetector.update()/.detect() 가 기대하는 피처 벡터
[bandwidth, latency, packet_loss, syn_ratio, unique_src_count, pkt_rate] 로 변환한다.

pkt_rate 집계 방식 (2026-09 재설계):
  - (기존, flow_start_window) 윈도우 내 플로우들의 총 패킷 수 / window_sec.
    플로우의 패킷 전량이 "시작 시각" 윈도우 하나에 귀속되어, 87µs짜리 공격 플로우
    (실제 ~40,000 pps)가 수백 pps로 축소되고, 103초짜리 BENIGN 플로우가 시작
    윈도우 하나를 비정상적으로 부풀리는 왜곡이 있었다 → recall 0.12의 주 원인.
  - (신규, flow_rate_sum) CICFlowMeter가 계산한 `Flow Packets/s` 컬럼을 사용해
    윈도우 내 플로우 전송률의 합으로 계산한다. 순간 pps의 상한 근사이지만
    공격(중앙값 ~40k pps)과 BENIGN(중앙값 ~0.5 pps)의 분리력을 보존한다.
    `Flow Packets/s`가 inf/NaN이면 duration으로 재계산하고, 그것도 불가하면
    제외 후 통계(stats)에 기록한다. 컬럼이 없는 배포본에서는 기존 방식으로
    폴백하며, 어느 모드였는지 stats["aggregation_mode"]에 남긴다.

알려진 한계:
  - bandwidth/latency/packet_loss는 CICDDoS2019에 대응 컬럼이 없음 →
    고정된 "정상" 기본값 사용 (시뮬레이션의 정상 트래픽 범위와 동일선상).
    즉 이 3개 차원은 IsolationForest 입력으로는 들어가지만 실제 검증 대상이 아니다.
  - syn_ratio/unique_src_count/pkt_rate 3개 차원만 실데이터로 진짜 검증된다.
  - 이 배포본의 `SYN Flag Count`는 공격 플로우에서도 거의 항상 0 (실측 비영 비율
    ~0.02%) — syn_ratio는 죽은 피처이며, 로더는 이를 stats로 계량해 보고한다.
"""
import os
from dataclasses import dataclass
from typing import Iterator, Optional

import numpy as np
import pandas as pd

# CICDDoS2019 컬럼명은 배포 버전에 따라 공백/대소문자가 다를 수 있어 정규화한다.
_COLUMN_ALIASES = {
    "timestamp":         ["Timestamp", " Timestamp"],
    "source_ip":         ["Source IP", " Source IP", "SrcIP"],
    "syn_flag_count":    ["SYN Flag Count", " SYN Flag Count"],
    "total_fwd_packets": ["Total Fwd Packets", " Total Fwd Packets"],
    "total_bwd_packets": ["Total Backward Packets", " Total Backward Packets"],
    "label":             ["Label", " Label"],
    # 선택 컬럼 — 있으면 pkt_rate를 플로우 전송률 합(flow_rate_sum)으로 계산
    "flow_duration":     ["Flow Duration", " Flow Duration"],
    "flow_packets_s":    ["Flow Packets/s", " Flow Packets/s"],
}
_REQUIRED_KEYS = [
    "timestamp", "source_ip", "syn_flag_count",
    "total_fwd_packets", "total_bwd_packets", "label",
]
_OPTIONAL_KEYS = ["flow_duration", "flow_packets_s"]

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
    keep = _REQUIRED_KEYS + [k for k in _OPTIONAL_KEYS if k in df.columns]
    return df[keep].copy()


def _to_epoch_seconds(series: pd.Series) -> pd.Series:
    # pandas 버전에 따라 datetime64 단위(ns/us/ms)가 달라 astype("int64")는
    # 단위가 일정하지 않다 — Timestamp 빼기로 단위 독립적인 epoch초를 계산한다.
    ts = pd.to_datetime(series, errors="coerce")
    return (ts - pd.Timestamp("1970-01-01")) / pd.Timedelta(seconds=1)


def _compute_pkt_rate(sub: pd.DataFrame, window_sec: float, stats: dict | None) -> float:
    """윈도우의 pkt_rate 계산.

    flow_rate_sum 모드: 윈도우 내 플로우들의 `Flow Packets/s` 합.
      - inf/NaN(주로 Flow Duration==0인 단발 플로우)은 duration으로 재계산 시도,
        불가하면 제외하고 stats에 집계한다 (조용히 0으로 만들지 않는다).
    폴백(flow_start_window): 총 패킷 수 / window_sec (기존 방식).
    """
    if "flow_packets_s" not in sub.columns:
        total = sub["total_fwd_packets"].sum() + sub["total_bwd_packets"].sum()
        return float(total / window_sec)

    rates     = pd.to_numeric(sub["flow_packets_s"], errors="coerce")
    flow_pkts = sub["total_fwd_packets"] + sub["total_bwd_packets"]
    bad       = ~np.isfinite(rates)

    if bad.any() and "flow_duration" in sub.columns:
        dur_s = pd.to_numeric(sub["flow_duration"], errors="coerce") / 1e6  # µs → s
        recomputable = bad & np.isfinite(dur_s) & (dur_s > 0)
        if recomputable.any():
            rates = rates.where(~recomputable, flow_pkts / dur_s)
            if stats is not None:
                stats["flows_pkt_rate_recomputed"] += int(recomputable.sum())

    finite = np.isfinite(rates)
    if stats is not None:
        stats["flows_pkt_rate_excluded"] += int((~finite).sum())
    return float(rates[finite].sum())


def _aggregate_window(
    window_id: int, sub: pd.DataFrame, window_sec: float, stats: dict | None = None,
) -> WindowResult:
    total_fwd     = sub["total_fwd_packets"].sum()
    total_bwd     = sub["total_bwd_packets"].sum()
    total_packets = total_fwd + total_bwd
    syn_count     = sub["syn_flag_count"].sum()

    pkt_rate  = _compute_pkt_rate(sub, window_sec, stats)
    syn_ratio = syn_count / max(total_packets, 1)
    unique_src = sub["source_ip"].nunique()

    labels = sub["label"].astype(str)
    non_benign = labels[labels != "BENIGN"]
    is_attack = not non_benign.empty
    attack_label = non_benign.mode().iloc[0] if is_attack else "BENIGN"

    if stats is not None:
        is_atk_flow = labels != "BENIGN"
        stats["attack_flows"]             += int(is_atk_flow.sum())
        stats["attack_flows_syn_nonzero"] += int((sub["syn_flag_count"][is_atk_flow] > 0).sum())

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
        # 집계 과정의 계량 정보 — 결과 JSON에 그대로 실어 재현/검증 근거로 남긴다.
        self.stats: dict = {
            "aggregation_mode":          None,  # "flow_rate_sum" | "flow_start_window"
            "flows_pkt_rate_recomputed": 0,     # inf/NaN → duration으로 재계산된 플로우 수
            "flows_pkt_rate_excluded":   0,     # 재계산 불가로 제외된 플로우 수
            "attack_flows":              0,
            "attack_flows_syn_nonzero":  0,     # syn_ratio 피처 유효성 실측 근거
        }

    def windows(self) -> Iterator[WindowResult]:
        carry: Optional[pd.DataFrame] = None

        for chunk in pd.read_csv(self.csv_path, chunksize=self.chunksize, low_memory=False):
            chunk = _normalize_columns(chunk)
            if self.stats["aggregation_mode"] is None:
                if "flow_packets_s" in chunk.columns:
                    self.stats["aggregation_mode"] = "flow_rate_sum"
                else:
                    self.stats["aggregation_mode"] = "flow_start_window"
                    print(
                        "  [경고] 'Flow Packets/s' 컬럼 없음 — pkt_rate를 기존 방식"
                        "(플로우 시작 윈도우 총패킷/초)으로 폴백합니다. 이 방식은 recall을"
                        " 크게 저하시키는 것으로 확인됨 (README 성능 검증 절 참고).",
                        flush=True,
                    )
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
                yield _aggregate_window(wid, sub, self.window_sec, self.stats)

            carry = chunk[chunk["_window_id"] == last_id]

        if carry is not None and not carry.empty:
            wid = carry["_window_id"].iloc[0]
            yield _aggregate_window(wid, carry, self.window_sec, self.stats)


def load_windows(csv_path: str, window_sec: float = 1.0) -> list[WindowResult]:
    """편의 함수: 전체 CSV를 윈도우 리스트로 변환 (권장 단일 공격파일 크기 기준 메모리에 적재 가능)."""
    return list(CICDDoSWindowIterator(csv_path, window_sec).windows())


def load_windows_with_stats(csv_path: str, window_sec: float = 1.0) -> tuple[list[WindowResult], dict]:
    """load_windows + 로더 집계 통계(aggregation_mode, 제외 플로우 수, SYN 실측치)를 함께 반환."""
    it = CICDDoSWindowIterator(csv_path, window_sec)
    windows = list(it.windows())
    return windows, dict(it.stats)


if __name__ == "__main__":
    # 로더 집계 로직(청크 경계 carry-over, pkt_rate 두 모드, inf 처리)을 점검하는 자가 테스트.
    tmp_path = "_cicddos_loader_selftest.csv"

    # ── 테스트 1: flow_rate_sum 모드 (Flow Duration / Flow Packets/s 존재) ──
    # 공격 플로우는 실데이터 특성(수 패킷 / 수십 µs → 수만 pps, SYN Flag Count=0)을 모사한다.
    csv_text = (
        "Timestamp,Source IP,SYN Flag Count,Total Fwd Packets,Total Backward Packets,"
        "Flow Duration,Flow Packets/s,Label\n"
        "2019-01-12 13:00:00.100000,10.0.0.1,1,5,3,2000000,4.0,BENIGN\n"
        "2019-01-12 13:00:00.200000,10.0.0.2,0,2,2,1000000,4.0,BENIGN\n"
        "2019-01-12 13:00:01.100000,10.0.0.3,0,2,2,87,45977.0,Syn\n"
        "2019-01-12 13:00:01.300000,10.0.0.4,0,2,0,0,inf,Syn\n"
        "2019-01-12 13:00:02.100000,10.0.0.5,1,4,4,3000000,2.67,BENIGN\n"
    )
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(csv_text)
    try:
        windows, stats = load_windows_with_stats(tmp_path, window_sec=1.0)
        assert len(windows) == 3, f"기대 윈도우 3개, 실제 {len(windows)}"
        assert stats["aggregation_mode"] == "flow_rate_sum", stats
        assert windows[0].is_attack is False
        assert windows[0].features["pkt_rate"] == 8.0, windows[0].features   # 4.0 + 4.0
        assert windows[1].is_attack is True and windows[1].attack_label == "Syn"
        assert windows[1].features["unique_src_count"] == 2.0
        # 핵심: 87µs/4패킷 공격 플로우의 실전송률(≈46k pps)이 임계치(10k)를 넘어야 한다.
        assert windows[1].features["pkt_rate"] >= 10000.0, windows[1].features
        # Flow Duration=0 → Flow Packets/s=inf → 재계산 불가 → 제외 1건 기록
        assert stats["flows_pkt_rate_excluded"] == 1, stats
        assert stats["attack_flows"] == 2 and stats["attack_flows_syn_nonzero"] == 0, stats
        assert windows[2].is_attack is False
        print("OK — self-test 1 (flow_rate_sum) 통과")
        for w in windows:
            print(f"  t={w.window_start:.0f} attack={w.is_attack} label={w.attack_label} features={w.features}")
        print(f"  stats={stats}")
    finally:
        os.remove(tmp_path)

    # ── 테스트 2: 폴백 모드 (선택 컬럼 없는 배포본 호환) ──
    csv_text_legacy = (
        "Timestamp,Source IP,SYN Flag Count,Total Fwd Packets,Total Backward Packets,Label\n"
        "2019-01-12 13:00:00.100000,10.0.0.1,1,5,3,BENIGN\n"
        "2019-01-12 13:00:01.100000,10.0.0.3,10,10,0,Syn\n"
        "2019-01-12 13:00:02.100000,10.0.0.5,1,4,4,BENIGN\n"
    )
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(csv_text_legacy)
    try:
        windows, stats = load_windows_with_stats(tmp_path, window_sec=1.0)
        assert len(windows) == 3, f"기대 윈도우 3개, 실제 {len(windows)}"
        assert stats["aggregation_mode"] == "flow_start_window", stats
        assert windows[1].features["pkt_rate"] == 10.0, windows[1].features  # 총패킷/1초 (기존 방식)
        print("OK — self-test 2 (flow_start_window 폴백) 통과")
    finally:
        os.remove(tmp_path)

    print("\n모든 자가 테스트 통과")
