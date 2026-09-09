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


def root_cause_analysis(
    diag: dict,
    ospf_costs: dict[str, int],
    bypass_cost: int = 100,
) -> str | None:
    """
    ZSM 3.1.1.2: Root Cause Analysis Service — diagnose() 결과에서 근본 원인 링크를 고른다.

    규칙 (2026-09-09 개정, cowork/AUDIT_2026-09-09.md P2):
      1. 이미 대응된(cost ≥ bypass_cost) 링크가 위반 노드 **전부**에 인접하면 → None.
         현재 위반은 그 링크의 잔여 스트레스(회복 중)로 본다 — 정상 링크를 건드리지 않는다.
      2. 미대응 링크 중 위반 노드 전부에 인접한 것이 있으면 → 그중 cost 최소. 확정 근본 원인.
      3. 둘 다 없으면(다중 혼잡 등 모호한 경우) 폴백: (-공유 위반노드 수, cost) 최소.
         이 경우는 추정일 뿐이며 api_server의 override 조건(공유 노드 ≥ 2)이 별도로 걸러낸다.

    개정 전에는 정답 링크가 cost 100이 되어 unhandled에서 빠진 2번째 사이클부터 남은
    인접 링크(알파벳순 첫 링크)를 근본 원인으로 지목해 정상 링크의 cost를 올렸다.
    """
    violated = set(diag.get("violated_nodes", []))
    suspected = list(diag.get("suspected_links", []))
    if not violated or not suspected:
        return None

    def shared(lk: str) -> int:
        return sum(1 for n in violated if lk in _NODE_LINKS.get(n, []))

    def cost(lk: str) -> int:
        return ospf_costs.get(lk, 10)

    covers_all = [lk for lk in suspected if shared(lk) == len(violated)]
    handled_all   = [lk for lk in covers_all if cost(lk) >= bypass_cost]
    unhandled_all = [lk for lk in covers_all if cost(lk) <  bypass_cost]

    if handled_all:
        # 규칙 1: 이미 조치된 링크가 위반 노드 전부를 덮는다 → 그 링크의 잔여 스트레스로 본다.
        # (새 혼잡이 생기면 그 링크의 양 끝이 함께 위반되어 covers_all이 달라지므로 놓치지 않는다)
        return None
    if unhandled_all:
        return min(unhandled_all, key=cost)           # 규칙 2: 확정 근본 원인

    unhandled = [lk for lk in suspected if cost(lk) < bypass_cost]
    candidates = unhandled or suspected
    return min(candidates, key=lambda lk: (-shared(lk), cost(lk)))   # 규칙 3: 폴백(추정)


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


# ── 자가 테스트 ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    def _m(node, lat, loss=0.0):
        return {"nodeId": node, "bandwidth": 500.0, "latency": lat, "packetLoss": loss}

    ok = lambda n: _m(n, 10.0)
    bad = lambda n: _m(n, 120.0)
    costs = {lk: 10 for lk in ["r1-r2", "r1-r3", "r2-r3", "r2-r4", "r3-r4", "r1-r4"]}

    # 사이클 1: r3-r4 혼잡 → 양 끝 노드 위반 → 규칙 2로 r3-r4 지목
    d = diagnose([ok("r1"), ok("r2"), bad("r3"), bad("r4")], costs)
    assert d["anomaly_detected"] and d["violated_nodes"] == ["r3", "r4"], d
    assert root_cause_analysis(d, costs) == "r3-r4", root_cause_analysis(d, costs)
    print("OK — 사이클 1: 근본 원인 r3-r4")

    # 사이클 2: r3-r4 cost=100 적용 후 잔여 위반 → 규칙 1로 None (정상 링크 건드리지 않음)
    costs2 = {**costs, "r3-r4": 100}
    d2 = diagnose([ok("r1"), ok("r2"), bad("r3"), bad("r4")], costs2)
    assert "r3-r4" not in d2["unhandled_links"], d2
    assert root_cause_analysis(d2, costs2) is None, root_cause_analysis(d2, costs2)
    print("OK — 사이클 2: 이미 대응됨 → None (개정 전에는 'r1-r3' 오판)")

    # 사이클 3: 한쪽 끝만 아직 위반 → 여전히 None
    d3 = diagnose([ok("r1"), ok("r2"), ok("r3"), bad("r4")], costs2)
    assert root_cause_analysis(d3, costs2) is None, root_cause_analysis(d3, costs2)
    print("OK — 사이클 3: 단일 노드 잔여 위반 → None")

    # 대응된 링크가 있어도 새 혼잡(r1-r2)이 생기면 그것을 지목
    d4 = diagnose([bad("r1"), bad("r2"), ok("r3"), ok("r4")], costs2)
    assert root_cause_analysis(d4, costs2) == "r1-r2", root_cause_analysis(d4, costs2)
    print("OK — 새 혼잡 r1-r2 지목")

    # 폴백(규칙 3): 위반 노드 3개를 모두 덮는 링크가 없음 → 추정
    d5 = diagnose([bad("r1"), ok("r2"), bad("r3"), bad("r4")], costs)
    rc5 = root_cause_analysis(d5, costs)
    assert rc5 in ("r1-r3", "r1-r4", "r3-r4"), rc5
    print(f"OK — 폴백(추정): {rc5}")

    # 이상 없음
    d6 = diagnose([ok("r1"), ok("r2"), ok("r3"), ok("r4")], costs)
    assert not d6["anomaly_detected"] and root_cause_analysis(d6, costs) is None
    print("OK — 정상 상태 → None")
    print("\n모든 자가 테스트 통과")
