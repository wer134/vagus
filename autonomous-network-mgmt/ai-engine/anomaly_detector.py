"""이상 감지 + 링크 진단 — Isolation Forest 기반."""
import numpy as np
from sklearn.ensemble import IsolationForest

SLA_LATENCY_MS    = 50.0
SLA_PACKET_LOSS   = 0.01

# 노드별 인접 링크 (metric_generator와 동일 토폴로지)
_NODE_LINKS: dict[str, list[str]] = {
    "r1": ["r1-r2", "r1-r3", "r1-r4"],
    "r2": ["r1-r2", "r2-r3", "r2-r4"],
    "r3": ["r1-r3", "r2-r3", "r3-r4"],
    "r4": ["r2-r4", "r3-r4", "r1-r4"],
}


class AnomalyDetector:
    def __init__(self, contamination: float = 0.05):
        self._model   = IsolationForest(contamination=contamination, random_state=42)
        self._trained = False
        self._buffer: list[list[float]] = []
        self._min_samples = 50

    def update(self, bandwidth: float, latency: float, packet_loss: float):
        self._buffer.append([bandwidth, latency, packet_loss])
        if len(self._buffer) >= self._min_samples:
            self._model.fit(np.array(self._buffer[-200:]))  # 최근 200개만 유지
            self._trained = True

    def is_anomaly(self, bandwidth: float, latency: float, packet_loss: float) -> bool:
        sla_breach = latency > SLA_LATENCY_MS or packet_loss > SLA_PACKET_LOSS
        if not self._trained:
            return sla_breach
        sample = np.array([[bandwidth, latency, packet_loss]])
        return self._model.predict(sample)[0] == -1 or sla_breach


def diagnose(
    metrics: list[dict],
    ospf_costs: dict[str, int],
) -> dict:
    """
    현재 메트릭 + OSPF 코스트를 분석해 문제를 진단한다.

    반환:
        anomaly_detected  : bool
        violated_nodes    : SLA 위반 노드 목록
        suspected_links   : 위반 노드 인접 링크 (문제 링크 후보)
        unhandled_links   : 의심 링크 중 아직 cost < 100 (미대응)
        severity          : "normal" | "warning" | "critical"
        reasoning         : 판단 근거 한 줄 요약
    """
    violated = [
        m for m in metrics
        if m.get("latency", 0) > SLA_LATENCY_MS
        or m.get("packetLoss", 0) > SLA_PACKET_LOSS
    ]

    if not violated:
        return {
            "anomaly_detected": False,
            "violated_nodes": [],
            "suspected_links": [],
            "unhandled_links": [],
            "severity": "normal",
            "reasoning": "전 노드 SLA 정상 — 조치 불필요",
        }

    # 위반 노드의 인접 링크 수집 → 의심 링크
    suspected: set[str] = set()
    for m in violated:
        nid = m.get("nodeId", "")
        for lk in _NODE_LINKS.get(nid, []):
            suspected.add(lk)

    # 의심 링크 중 아직 cost < 100인 것 (우선 대응 대상)
    unhandled = [lk for lk in sorted(suspected) if ospf_costs.get(lk, 10) < 100]

    v_nodes   = [m["nodeId"] for m in violated]
    max_lat   = max(m.get("latency", 0) for m in violated)
    max_loss  = max(m.get("packetLoss", 0) for m in violated)
    severity  = "critical" if max_lat > 100 or max_loss > 0.05 else "warning"

    reasoning = (
        f"SLA 위반 노드={v_nodes}  "
        f"(lat_max={max_lat:.1f}ms, loss_max={max_loss*100:.2f}%)  →  "
        f"의심 링크={sorted(suspected)}  →  "
        f"미대응={unhandled}"
    )

    return {
        "anomaly_detected": True,
        "violated_nodes": v_nodes,
        "suspected_links": sorted(suspected),
        "unhandled_links": unhandled,
        "severity": severity,
        "reasoning": reasoning,
    }


class SecurityAnomalyDetector:
    """
    DDoS/포트스캔 탐지 전용 Isolation Forest.

    피처: bandwidth, latency, packet_loss, syn_ratio, unique_src_count, pkt_rate
    임계치 기반 규칙 + IF 모델을 조합해 공격 유형까지 추론한다.
    """

    _THRESHOLDS = {
        "syn_ratio":        0.30,    # SYN 비율 30% 초과 → DDoS SYN-flood 의심
        "unique_src_count": 500.0,   # 5초 내 500 IP 초과 → 포트스캔 의심
        "pkt_rate":         10000.0, # 10k pps 초과 → DDoS 의심
    }

    def __init__(self, contamination: float = 0.05):
        self._model   = IsolationForest(contamination=contamination, random_state=42)
        self._trained = False
        self._buffer: list[list[float]] = []
        self._min_samples = 30

    def update(
        self,
        bandwidth: float, latency: float, packet_loss: float,
        syn_ratio: float = 0.0, unique_src_count: float = 0.0, pkt_rate: float = 0.0,
    ) -> None:
        self._buffer.append([bandwidth, latency, packet_loss,
                              syn_ratio, unique_src_count, pkt_rate])
        if len(self._buffer) >= self._min_samples:
            self._model.fit(np.array(self._buffer[-200:]))
            self._trained = True

    def detect(
        self,
        bandwidth: float, latency: float, packet_loss: float,
        syn_ratio: float = 0.0, unique_src_count: float = 0.0, pkt_rate: float = 0.0,
    ) -> dict:
        """
        Returns:
            is_threat:   bool
            attack_type: "ddos" | "portscan" | "unknown" | None
            score:       float  (높을수록 이상)
            triggers:    list[str]  (임계치 초과 피처 목록)
        """
        t = self._THRESHOLDS
        triggers = [
            feat for feat, val in [
                ("syn_ratio",        syn_ratio),
                ("unique_src_count", unique_src_count),
                ("pkt_rate",         pkt_rate),
            ]
            if val >= t[feat]
        ]

        score = 0.0
        is_if_anomaly = False
        if self._trained:
            sample = np.array([[bandwidth, latency, packet_loss,
                                 syn_ratio, unique_src_count, pkt_rate]])
            is_if_anomaly = self._model.predict(sample)[0] == -1
            score = float(-self._model.score_samples(sample)[0])

        is_threat = bool(triggers) or is_if_anomaly

        attack_type: str | None = None
        if is_threat:
            if "unique_src_count" in triggers:
                attack_type = "portscan"
            elif "pkt_rate" in triggers or "syn_ratio" in triggers:
                attack_type = "ddos"
            else:
                attack_type = "unknown"

        return {
            "is_threat":   is_threat,
            "attack_type": attack_type,
            "score":       round(score, 4),
            "triggers":    triggers,
        }


# 싱글턴 (api_server.py 공유)
detector = AnomalyDetector()
security_detector = SecurityAnomalyDetector()
