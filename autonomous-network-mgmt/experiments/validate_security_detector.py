"""
CICDDoS2019 실데이터로 SecurityAnomalyDetector(DDoS 탐지) 검증.

기존 시뮬레이션(metric_generator.py)은 syn_ratio/unique_src_count/pkt_rate를
가상의 난수로 생성한다. 이 스크립트는 실제 CICFlowMeter 플로우 데이터를
1초 윈도우로 집계해 동일한 SecurityAnomalyDetector 클래스(.update/.detect)에
그대로 흘려보내 precision/recall/F1을 측정한다.

알려진 한계 (cicddos_loader.py, README 참고):
  - bandwidth/latency/packet_loss는 고정 placeholder — 검증 대상이 아님
  - CICDDoS2019에는 포트스캔 공격이 없음 — attack_type="portscan" 분기는 검증 불가
  - 공격일 CSV는 대부분 공격 트래픽 — --benign-warmup으로 cold-start 민감도 완화
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai-engine"))

from anomaly_detector import SecurityAnomalyDetector  # noqa: E402

from cicddos_loader import load_windows  # noqa: E402

from sklearn.metrics import (  # noqa: E402
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)

RESULT_DIR = os.path.join(os.path.dirname(__file__), "results")


def _build_eval_sequence(windows: list, benign_warmup: int) -> list:
    """benign_warmup > 0이면 앞쪽 BENIGN 윈도우 N개를 시퀀스 맨 앞에 한 번 더 재생한다.

    공격일 CSV는 대부분 공격 트래픽이라 _min_samples=30 cold-start 구간에서
    IsolationForest가 공격 트래픽을 '정상'으로 학습할 위험이 있다. 배포 시
    이미 정상 베이스라인을 학습한 모델을 투입하는 상황을 모사한다.
    """
    if benign_warmup <= 0:
        return windows
    benign = [w for w in windows if not w.is_attack]
    used = benign[:benign_warmup]
    if len(used) < benign_warmup:
        print(
            f"  [경고] BENIGN 윈도우가 {len(benign)}개뿐 — "
            f"요청한 {benign_warmup}개 대신 {len(used)}개만 warmup에 사용",
            flush=True,
        )
    return used + windows


def run_validation(
    csv_path: str,
    window_sec: float,
    max_windows: int | None,
    benign_warmup: int,
    contamination: float,
    plot_path: str | None = None,
) -> dict:
    print(f"CICDDoS2019 로딩 중: {csv_path} (window_sec={window_sec})", flush=True)
    windows = load_windows(csv_path, window_sec=window_sec)
    if max_windows is not None:
        windows = windows[:max_windows]
    n_benign = sum(1 for w in windows if not w.is_attack)
    n_attack = len(windows) - n_benign
    print(f"  윈도우 {len(windows)}개 (BENIGN={n_benign}, 공격={n_attack})", flush=True)

    eval_seq = _build_eval_sequence(windows, benign_warmup)
    actual_warmup = min(benign_warmup, n_benign) if benign_warmup > 0 else 0

    detector = SecurityAnomalyDetector(contamination=contamination)
    y_true, y_pred = [], []
    type_true, type_pred = [], []
    trained_flags = []  # 윈도우별 IsolationForest 학습 완료 여부 (cold-start 구간 표시용)

    for w in eval_seq:
        detector.update(**w.features)
        result = detector.detect(**w.features)
        y_true.append(w.is_attack)
        y_pred.append(result["is_threat"])
        type_true.append("ddos" if w.is_attack else "none")
        type_pred.append(result["attack_type"] or "none")
        trained_flags.append(detector._trained)

    metrics = {
        "precision": round(precision_score(y_true, y_pred, zero_division=0), 4),
        "recall":    round(recall_score(y_true, y_pred, zero_division=0), 4),
        "f1":        round(f1_score(y_true, y_pred, zero_division=0), 4),
        "confusion_matrix": confusion_matrix(y_true, y_pred).tolist(),
        "attack_type_report": classification_report(
            type_true, type_pred, zero_division=0, output_dict=True,
        ),
    }

    portscan_predicted = sum(1 for t in type_pred if t == "portscan")

    result_doc = {
        "dataset": {
            "name":          "CICDDoS2019",
            "file":          os.path.basename(csv_path),
            "source":        "https://www.unb.ca/cic/datasets/ddos-2019.html",
            "window_sec":    window_sec,
            "benign_warmup": actual_warmup,
            "n_windows":     len(eval_seq),
            "n_benign_windows": n_benign,
            "n_attack_windows": n_attack,
        },
        "metrics": metrics,
        "portscan_predicted_count": portscan_predicted,
        "known_limitations": [
            "bandwidth/latency/packet_loss는 고정 placeholder — 실검증 대상은 syn_ratio/unique_src_count/pkt_rate 3개 차원뿐",
            "CICDDoS2019에는 포트스캔 공격이 없음 — attack_type='portscan' 분기는 이 데이터셋으로 검증 불가 (실패 아님)",
            "공격일 CSV는 BENIGN 비중이 낮아 cold-start 학습이 민감함 — benign_warmup 값으로 결과가 달라질 수 있음",
        ],
        "timestamp": datetime.now().isoformat(),
    }

    _print_summary(csv_path, result_doc)

    if plot_path is not None:
        _plot_learning_curve(y_true, y_pred, trained_flags, plot_path)
        print(f"Plot saved -> {plot_path}", flush=True)

    return result_doc


def _plot_learning_curve(
    y_true: list[bool], y_pred: list[bool], trained_flags: list[bool],
    out_path: str, rolling: int = 200,
) -> None:
    """탐지 과정을 2단 그래프로 시각화한다.

    위: 윈도우별 실제 공격 vs 탐지기 판정 (회색 음영 = IsolationForest 미학습 cold-start 구간)
    아래: 최근 `rolling`개 윈도우 기준 F1 추이 — 데이터를 더 볼수록 탐지력이 좋아지는지 확인.
    베이스레이트(전부 공격으로 찍었을 때의 F1)를 점선으로 함께 표시해 "진짜 학습 효과"와
    "단순 클래스 비율 우연"을 구분한다.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from collections import deque

    # 한글 라벨이 깨지지 않도록 OS에 있는 한글 폰트를 사용 (Windows: 맑은 고딕)
    for _font in ("Malgun Gothic", "AppleGothic", "NanumGothic"):
        if _font in {f.name for f in matplotlib.font_manager.fontManager.ttflist}:
            matplotlib.rcParams["font.family"] = _font
            break
    matplotlib.rcParams["axes.unicode_minus"] = False

    n = len(y_true)
    idx = list(range(n))
    base_rate = sum(y_true) / n if n else 0.0
    base_rate_f1 = (
        2 * base_rate / (base_rate + 1) if base_rate else 0.0
    )  # "전부 공격" 예측 시 precision=base_rate, recall=1.0 인 F1

    buf_true: deque = deque(maxlen=rolling)
    buf_pred: deque = deque(maxlen=rolling)
    rolling_f1 = []
    for yt, yp in zip(y_true, y_pred):
        buf_true.append(yt)
        buf_pred.append(yp)
        tp = sum(1 for t, p in zip(buf_true, buf_pred) if t and p)
        fp = sum(1 for t, p in zip(buf_true, buf_pred) if not t and p)
        fn = sum(1 for t, p in zip(buf_true, buf_pred) if t and not p)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec  = tp / (tp + fn) if (tp + fn) else 0.0
        rolling_f1.append(2 * prec * rec / (prec + rec) if (prec + rec) else 0.0)

    cold_start_end = next((i for i, t in enumerate(trained_flags) if t), n)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)

    if cold_start_end > 0:
        ax1.axvspan(0, cold_start_end, color="lightgray", alpha=0.5, label="cold-start (미학습)")
    ax1.step(idx, [1.05 if v else 0.05 for v in y_true], where="post",
              label="실제 공격(ground truth)", color="tab:red", alpha=0.8, linewidth=1)
    ax1.step(idx, [0.95 if v else -0.05 for v in y_pred], where="post",
              label="탐지기 판정(is_threat)", color="tab:blue", alpha=0.6, linewidth=1)
    ax1.set_ylabel("공격 여부")
    ax1.set_yticks([0, 1])
    ax1.set_ylim(-0.2, 1.2)
    ax1.legend(loc="upper right", fontsize=8)
    ax1.set_title("CICDDoS2019 검증 — 윈도우별 실제 공격 vs 탐지기 판정")

    ax2.plot(idx, rolling_f1, color="tab:green", label=f"Rolling F1 (최근 {rolling}개 윈도우)")
    ax2.axhline(base_rate_f1, color="gray", linestyle="--",
                label=f"베이스레이트 F1 (항상 공격 예측, base_rate={base_rate:.2f})")
    ax2.set_ylabel("F1")
    ax2.set_xlabel("윈도우 인덱스 (시간순)")
    ax2.set_ylim(0, 1)
    ax2.legend(loc="upper right", fontsize=8)
    ax2.set_title("탐지 성능 추이 — 데이터를 더 볼수록 좋아지는지 확인")

    fig.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def _print_summary(csv_path: str, doc: dict) -> None:
    d, m = doc["dataset"], doc["metrics"]
    title = f"CICDDoS2019 Validation — {d['file']} ({d['n_windows']} windows)"
    width = max(54, len(title) + 4)
    print(f"\n{'=' * width}", flush=True)
    print(f"  {title}", flush=True)
    print(f"{'=' * width}", flush=True)
    print(f"  BENIGN warmup        : {d['benign_warmup']}", flush=True)
    print(f"  Precision (is_threat): {m['precision']}", flush=True)
    print(f"  Recall    (is_threat): {m['recall']}", flush=True)
    print(f"  F1        (is_threat): {m['f1']}", flush=True)
    print(f"  Confusion Matrix [[TN FP] [FN TP]]:", flush=True)
    for row in m["confusion_matrix"]:
        print(f"    {row}", flush=True)
    print(f"  {'-' * (width - 2)}", flush=True)
    print(
        f"  attack_type 예측 중 portscan={doc['portscan_predicted_count']}건 "
        f"(데이터셋에 포트스캔 ground truth 없음 — N/A)",
        flush=True,
    )
    print(f"{'=' * width}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-path", required=True, help="CICDDoS2019 CSV 파일 경로")
    parser.add_argument("--window-sec", type=float, default=1.0)
    parser.add_argument("--max-windows", type=int, default=None, help="디버그용 상한")
    parser.add_argument(
        "--benign-warmup", type=int, default=0,
        help="BENIGN 윈도우를 앞쪽에 N개까지 재생해 cold-start 학습 보장 (기본 0=순수 시간순)",
    )
    parser.add_argument("--contamination", type=float, default=0.05)
    parser.add_argument("--output", default="cicddos_validation.json")
    parser.add_argument(
        "--no-plot", action="store_true",
        help="탐지 과정 시각화(PNG) 생성을 생략 (기본은 생성함)",
    )
    args = parser.parse_args()

    plot_path = None
    if not args.no_plot:
        plot_name = os.path.splitext(args.output)[0] + "_curve.png"
        plot_path = os.path.join(RESULT_DIR, plot_name)

    result_doc = run_validation(
        csv_path=args.csv_path,
        window_sec=args.window_sec,
        max_windows=args.max_windows,
        benign_warmup=args.benign_warmup,
        contamination=args.contamination,
        plot_path=plot_path,
    )

    os.makedirs(RESULT_DIR, exist_ok=True)
    out_path = os.path.join(RESULT_DIR, args.output)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result_doc, f, indent=2, ensure_ascii=False)
    print(f"\nSaved -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
