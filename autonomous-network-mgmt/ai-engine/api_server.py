"""
FastAPI — ZSM/ENI 폐쇄 루프 자율 제어 서버

ZSM 3.1.1 관리 서비스 구현:
  - Data Collection  : _fetch_snmp()
  - Analytics        : diagnose() — 이상탐지 + 근본원인분석
  - Intelligence     : MAML adapt_and_predict() — 의사결정
  - Orchestration    : _apply_ospf() — 행동 실행
  - AI Model Eval    : ModelPerformanceTracker — 모델 성능 자가진단

ENI 폐쇄 제어 루프 (OODA):
  Observe  → _fetch_snmp()
  Orient   → diagnose() + root_cause_analysis()
  Decide   → MAML 행동 결정
  Act      → _apply_ospf()
"""
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from statistics import mean, stdev

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from anomaly_detector import detector, diagnose, security_detector
from ospf_security import ospf_monitor
from agents.baseline_drl import BaselineAgent
from agents.few_shot_agent import FewShotAgent
from environment.network_env import LINKS, OSPF_COSTS, MAX_BW, MAX_LAT, MAX_COST

SNMP_URL = os.environ.get("SNMP_URL", "http://127.0.0.1:5001")

baseline_agent = BaselineAgent()
few_shot_agent = FewShotAgent()

# ── ZSM 3.1.1.4: 배포된 AI 모델 성능 자가 추적 ───────────────────────────────
class ModelPerformanceTracker:
    """
    ZSM: 'Deployed AI Model Performance Evaluation Service'
    MAML 모델의 실시간 성능을 추적하고 재학습 필요 여부를 판단한다.
    """
    def __init__(self, window: int = 20):
        self._rewards:      deque = deque(maxlen=window)
        self._ttr_list:     deque = deque(maxlen=window)
        self._sla_viol:     deque = deque(maxlen=window)
        self._anomaly_start: int | None = None
        self._step:         int = 0
        self._retrain_threshold_ttr = 50   # avg TTR > 50 → 재학습 권고
        self._retrain_threshold_sla = 0.7  # SLA 위반율 > 70% → 재학습 권고

    def record(self, reward: float, sla_violated: bool, resolved: bool = False):
        self._rewards.append(reward)
        self._sla_viol.append(1 if sla_violated else 0)
        self._step += 1
        if sla_violated and self._anomaly_start is None:
            self._anomaly_start = self._step
        elif not sla_violated and self._anomaly_start is not None:
            ttr = self._step - self._anomaly_start
            self._ttr_list.append(ttr)
            self._anomaly_start = None

    def status(self) -> dict:
        avg_reward = mean(self._rewards) if self._rewards else 0.0
        avg_ttr    = mean(self._ttr_list) if self._ttr_list else 0.0
        sla_rate   = mean(self._sla_viol) if self._sla_viol else 0.0
        needs_retrain = avg_ttr > self._retrain_threshold_ttr or \
                        sla_rate > self._retrain_threshold_sla
        return {
            "avg_reward":    round(avg_reward, 3),
            "avg_ttr":       round(avg_ttr, 1),
            "sla_viol_rate": round(sla_rate, 3),
            "needs_retrain": needs_retrain,
            "steps":         self._step,
        }


perf_tracker = ModelPerformanceTracker()

# 자율 루프 support buffer (MAML inner-loop)
_support_buffer: list[tuple] = []
_MAX_SUPPORT    = 32
_prev_obs: np.ndarray | None = None
_prev_action: int = 0
_prev_sla_violated = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    import sys
    out = lambda s: print(s, file=sys.stdout, flush=True)
    out("=" * 55)
    out("  ANM AI Engine - ZSM/ENI Closed-Loop Server")
    out("=" * 55)
    out(f"  Baseline PPO : {'ready' if baseline_agent.is_ready() else 'NOT trained'}")
    out(f"  MAML Few-shot: {'ready' if few_shot_agent.is_ready() else 'NOT trained'}")
    out(f"  SNMP target  : {SNMP_URL}")
    out("=" * 55)
    yield


app = FastAPI(title="ANM AI Engine — ZSM/ENI", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ── 스키마 ─────────────────────────────────────────────────────────────────────

class MetricPayload(BaseModel):
    nodeId:     str
    bandwidth:  float
    latency:    float
    packetLoss: float

class StatePayload(BaseModel):
    bandwidths: list[float]
    latencies:  list[float]
    ospfCosts:  list[float]  # 6개: r1-r2,r1-r3,r2-r3,r2-r4,r3-r4,r1-r4
    useFewShot: bool = False

class ActionResponse(BaseModel):
    targetLink:  str
    newOspfCost: int
    agentType:   str

class DiagnosisResponse(BaseModel):
    anomaly_detected: bool
    violated_nodes:   list[str]
    suspected_links:  list[str]
    unhandled_links:  list[str]
    root_cause_link:  str | None   # ZSM: Root Cause Analysis
    severity:         str
    reasoning:        str

class LsaCheckPayload(BaseModel):
    router_id: str
    seq_no:    int

class SecurityMetricPayload(BaseModel):
    nodeId:           str
    bandwidth:        float
    latency:          float
    packetLoss:       float
    syn_ratio:        float = 0.0
    unique_src_count: float = 0.0
    pkt_rate:         float = 0.0

class OODAStepResponse(BaseModel):
    """ENI 폐쇄 루프 1 사이클 전체 응답 (OODA 라벨 포함)."""
    # Observe
    observe: dict
    # Orient
    orient:  DiagnosisResponse
    # Decide
    decide:  dict
    # Act
    act:     dict
    # 모델 자가진단 (ZSM AI Model Eval)
    model_status: dict
    # 전체 판단 근거
    reasoning_chain: str


# ── 헬퍼 ───────────────────────────────────────────────────────────────────────

def _obs_from_payload(bws, lats, costs) -> np.ndarray:
    return np.array(bws + lats + costs, dtype=np.float32)

def _metrics_to_obs(metrics: list[dict], ospf_map: dict) -> np.ndarray:
    LINK_ORDER = ["r1-r2", "r1-r3", "r2-r3", "r2-r4", "r3-r4", "r1-r4"]
    NODES      = ["r1", "r2", "r3", "r4"]
    mm = {m["nodeId"]: m for m in metrics}
    bws   = [mm.get(n, {}).get("bandwidth",  500.0) / MAX_BW  for n in NODES]
    lats  = [mm.get(n, {}).get("latency",     10.0) / MAX_LAT for n in NODES]
    costs = [ospf_map.get(lk, 10) / MAX_COST for lk in LINK_ORDER]
    return np.clip(np.array(bws + lats + costs, dtype=np.float32), 0.0, 1.0)

def _decode_action(action_idx: int):
    link_idx, cost_idx = divmod(action_idx, len(OSPF_COSTS))
    return LINKS[link_idx], OSPF_COSTS[cost_idx]

def _fetch_snmp() -> tuple[list[dict], dict]:
    try:
        with httpx.Client(timeout=2.0) as c:
            metrics  = c.get(f"{SNMP_URL}/metrics").json()
            ospf_raw = c.get(f"{SNMP_URL}/ospf/costs").json()
            ospf_map = {k: int(v) for k, v in ospf_raw.items()}
        return metrics, ospf_map
    except Exception:
        NODES = ["r1", "r2", "r3", "r4"]
        return (
            [{"nodeId": n, "bandwidth": 500.0, "latency": 10.0, "packetLoss": 0.0} for n in NODES],
            {lk: 10 for lk in ["r1-r2","r1-r3","r2-r3","r2-r4","r3-r4","r1-r4"]},
        )

def _apply_ospf(link: str, cost: int):
    try:
        with httpx.Client(timeout=2.0) as c:
            c.put(f"{SNMP_URL}/ospf/costs/{link}", json={"cost": cost})
    except Exception:
        pass

def _root_cause_analysis(diag: dict, ospf_map: dict) -> str | None:
    """
    ZSM 3.1.1.2: Root Cause Analysis Service

    우선순위 (높을수록 root cause 가능성 높음):
      1. 위반 노드 수가 가장 많이 공유되는 링크 (공통 병목)
      2. OSPF cost 낮은 링크 (트래픽 집중)
    """
    from anomaly_detector import _NODE_LINKS
    candidates = diag.get("unhandled_links", []) or diag.get("suspected_links", [])
    if not candidates:
        return None

    violated = set(diag.get("violated_nodes", []))

    def score(lk: str) -> tuple:
        # 위반 노드 양쪽이 모두 이 링크에 인접하면 점수 높음
        shared = sum(1 for n in violated if lk in _NODE_LINKS.get(n, []))
        cost   = ospf_map.get(lk, 10)
        # (공유 노드 수 많을수록 ↑, cost 낮을수록 ↑)
        return (-shared, cost)

    return min(candidates, key=score)


# ── 엔드포인트 ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status":         "ok",
        "baseline_ready": baseline_agent.is_ready(),
        "maml_ready":     few_shot_agent.is_ready(),
        "model_status":   perf_tracker.status(),
    }

@app.post("/anomaly")
def analyze_anomaly(metric: MetricPayload):
    detector.update(metric.bandwidth, metric.latency, metric.packetLoss)
    is_anom = detector.is_anomaly(metric.bandwidth, metric.latency, metric.packetLoss)
    return {"isAnomaly": is_anom, "nodeId": metric.nodeId}

@app.post("/action", response_model=ActionResponse)
def decide_action(state: StatePayload):
    """수동 행동 결정 (대시보드 버튼용)."""
    obs = _obs_from_payload(state.bandwidths, state.latencies, state.ospfCosts)
    if state.useFewShot:
        if not few_shot_agent.is_ready():
            raise HTTPException(503, "Few-shot agent not trained")
        if len(_support_buffer) >= 4:
            action_idx = few_shot_agent.adapt_and_predict(_support_buffer[-32:], obs, adapt_steps=2)
        else:
            action_idx = few_shot_agent.predict(obs)
        agent_type = "maml"
    else:
        if not baseline_agent.is_ready():
            raise HTTPException(503, "Baseline agent not trained")
        action_idx = baseline_agent.predict(obs)
        agent_type = "ppo"
    link, cost = _decode_action(action_idx)
    return ActionResponse(targetLink=link, newOspfCost=cost, agentType=agent_type)

@app.get("/diagnose", response_model=DiagnosisResponse)
def diagnose_now():
    metrics, ospf_map = _fetch_snmp()
    for m in metrics: detector.update(m["bandwidth"], m["latency"], m["packetLoss"])
    result = diagnose(metrics, ospf_map)
    result["root_cause_link"] = _root_cause_analysis(result, ospf_map)
    return DiagnosisResponse(**result)

@app.get("/model-status")
def model_status():
    """ZSM 3.1.1.4: Deployed AI Model Performance Evaluation."""
    return perf_tracker.status()

@app.post("/auto-step", response_model=OODAStepResponse)
def auto_step(disable_analytics: bool = False, disable_maml: bool = False):
    """
    ENI 폐쇄 루프 1 사이클 — OODA 패러다임 완전 구현.

    Observe : SNMP 메트릭 수집
    Orient  : 이상 감지 + 근본 원인 분석 (ZSM Analytics)
    Decide  : MAML inner-loop 적응 → 행동 결정 (ZSM Intelligence)
    Act     : OSPF 변경 실행 (ZSM Orchestration)
    + ZSM AI Model Eval: 모델 성능 자가진단
    """
    global _support_buffer, _prev_obs, _prev_action, _prev_sla_violated

    # ══ Observe ══════════════════════════════════════════════════════
    t0 = time.time()
    metrics, ospf_map = _fetch_snmp()
    obs = _metrics_to_obs(metrics, ospf_map)
    for m in metrics: detector.update(m["bandwidth"], m["latency"], m["packetLoss"])

    observe_out = {
        "nodes": {m["nodeId"]: {
            "latency_ms":   round(m["latency"],    2),
            "bandwidth_mb": round(m["bandwidth"],  1),
            "packet_loss":  round(m["packetLoss"], 4),
        } for m in metrics},
        "ospf": ospf_map,
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
    }

    # ══ Orient — 이상 감지 + 근본 원인 분석 ══════════════════════════
    diag = diagnose(metrics, ospf_map)
    root_cause = _root_cause_analysis(diag, ospf_map)
    diag["root_cause_link"] = root_cause
    orient_out = DiagnosisResponse(**diag)

    # reward 계산 + support buffer 갱신
    from reward import compute_reward
    reward = compute_reward(
        latencies     = [m["latency"]    for m in metrics],
        bandwidths    = [m["bandwidth"]  for m in metrics],
        packet_losses = [m["packetLoss"] for m in metrics],
    )
    sla_violated = diag["anomaly_detected"]
    perf_tracker.record(reward, sla_violated)

    if _prev_obs is not None:
        _support_buffer.append((_prev_obs, _prev_action, float(reward)))
        if len(_support_buffer) > _MAX_SUPPORT:
            _support_buffer.pop(0)
    _prev_obs = obs.copy()

    # ══ 이상 없으면 조치 없이 반환 ═══════════════════════════════════
    if not diag["anomaly_detected"]:
        chain = (
            f"[Observe] 전 노드 정상  →  "
            f"[Orient] SLA 위반 없음  →  "
            f"[Decide] 조치 불필요  →  "
            f"[Act] OSPF 유지"
        )
        return OODAStepResponse(
            observe=observe_out, orient=orient_out,
            decide={"action": None, "reason": "no anomaly"},
            act={"applied": False},
            model_status=perf_tracker.status(),
            reasoning_chain=chain,
        )

    # ══ Decide — MAML 행동 결정 ══════════════════════════════════════
    if disable_maml:
        # Analytics-only: root cause에 cost=100 직접 적용
        if root_cause:
            link = root_cause
            cost = 100
        else:
            unhandled = diag.get("unhandled_links") or diag.get("suspected_links", [])
            link = unhandled[0] if unhandled else LINKS[0]
            cost = 100
        action_idx = LINKS.index(link) * len(OSPF_COSTS) + (OSPF_COSTS.index(100) if 100 in OSPF_COSTS else 3)
        adapt_note = "Analytics-only (MAML disabled)"
        _prev_action = action_idx
        _apply_ospf(link, cost)
        chain = (
            f"[Observe] 위반노드={diag['violated_nodes']}  →  "
            f"[Orient] 근본원인={root_cause}  →  "
            f"[Decide/Analytics-only] {link} cost={cost}  →  "
            f"[Act] OSPF 적용 완료"
        )
        return OODAStepResponse(
            observe=observe_out, orient=orient_out,
            decide={"action": {"link": link, "cost": cost}, "adapt_note": adapt_note},
            act={"applied": True, "link": link, "cost": cost},
            model_status=perf_tracker.status(),
            reasoning_chain=chain,
        )

    if not few_shot_agent.is_ready():
        return OODAStepResponse(
            observe=observe_out, orient=orient_out,
            decide={"action": None, "reason": "MAML not trained"},
            act={"applied": False},
            model_status=perf_tracker.status(),
            reasoning_chain=diag["reasoning"] + "  →  [Decide] MAML 미학습",
        )

    if len(_support_buffer) >= 4:
        action_idx = few_shot_agent.adapt_and_predict(
            _support_buffer[-32:], obs, adapt_steps=3
        )
        adapt_note = f"inner-loop 적응 ({len(_support_buffer)} samples)"
    else:
        action_idx = few_shot_agent.predict(obs)
        adapt_note = "meta-init 직접 사용"

    link, cost = _decode_action(action_idx)

    # ZSM Analytics → Intelligence 가이드:
    # root cause가 고신뢰(위반 노드 양쪽 인접)이고 MAML 결정이 다른 링크이면 보정.
    alignment = ""
    if not disable_analytics and root_cause and root_cause != link and root_cause in LINKS:
        violated_set = set(diag.get("violated_nodes", []))
        from anomaly_detector import _NODE_LINKS
        nodes_sharing_root = sum(1 for n in violated_set if root_cause in _NODE_LINKS.get(n, []))
        if nodes_sharing_root >= 2:
            rc_idx   = LINKS.index(root_cause)
            cost_idx = OSPF_COSTS.index(100) if 100 in OSPF_COSTS else len(OSPF_COSTS) - 2
            action_idx = rc_idx * len(OSPF_COSTS) + cost_idx
            link, cost = LINKS[rc_idx], OSPF_COSTS[cost_idx]
            alignment = f" [Analytics override: root_cause={root_cause}]"
            adapt_note += " + Analytics-guided"
        else:
            alignment = f" (근본원인={root_cause} vs MAML={link})"

    _prev_action = action_idx  # support buffer에 실제 action 기록

    # ══ Act — OSPF 변경 ══════════════════════════════════════════════
    _apply_ospf(link, cost)

    chain = (
        f"[Observe] 위반노드={diag['violated_nodes']}  →  "
        f"[Orient] 의심링크={diag['suspected_links']} 근본원인={root_cause}  →  "
        f"[Decide/{adapt_note}] {link} cost={cost}{alignment}  →  "
        f"[Act] OSPF 적용 완료"
    )

    model_st = perf_tracker.status()
    if model_st["needs_retrain"]:
        chain += "  ⚠ 모델 성능 저하 감지 — 재학습 권고"

    return OODAStepResponse(
        observe=observe_out,
        orient=orient_out,
        decide={"action": {"link": link, "cost": cost}, "adapt_note": adapt_note},
        act={"applied": True, "link": link, "cost": cost},
        model_status=model_st,
        reasoning_chain=chain,
    )

# ── OSPF 보안 ──────────────────────────────────────────────────────────────────

@app.post("/ospf/lsa-check")
def lsa_check(payload: LsaCheckPayload):
    """
    LSA 수신 시 위조 여부 검사.

    탐지 규칙: 미등록 라우터 ID / 시퀀스 번호 점프 / LSA flooding
    """
    return ospf_monitor.check_lsa(payload.router_id, payload.seq_no)


@app.get("/ospf/security-status")
def ospf_security_status():
    """최근 OSPF 보안 알림 목록 반환."""
    return {"recent_alerts": ospf_monitor.recent_alerts(20)}


@app.post("/debug/fake-lsa")
def inject_fake_lsa(router_id: str = "r99", seq_no: int = 99999):
    """데모용: 위조 LSA 주입 시뮬레이션."""
    return ospf_monitor.inject_fake_lsa(router_id, seq_no)


# ── 트래픽 기반 보안 탐지 ───────────────────────────────────────────────────────

@app.post("/security/detect")
def security_detect(metric: SecurityMetricPayload):
    """
    보안 피처 기반 공격 탐지.

    IsolationForest + 임계치 규칙으로 DDoS / 포트스캔을 탐지하고
    공격 확인 시 시뮬레이션 OpenFlow 차단 룰을 반환한다.
    """
    security_detector.update(
        metric.bandwidth, metric.latency, metric.packetLoss,
        metric.syn_ratio, metric.unique_src_count, metric.pkt_rate,
    )
    result = security_detector.detect(
        metric.bandwidth, metric.latency, metric.packetLoss,
        metric.syn_ratio, metric.unique_src_count, metric.pkt_rate,
    )
    response_action = None
    if result["is_threat"]:
        response_action = {
            "type":   "openflow_block",
            "match":  {"node": metric.nodeId, "attack_type": result["attack_type"]},
            "action": "DROP",
            "note":   "시뮬레이션 OpenFlow 차단 룰 (데모)",
        }
    return {**result, "nodeId": metric.nodeId, "response_action": response_action}


@app.get("/security/status")
def security_status():
    """통합 보안 상태: OSPF 알림 + 트래픽 탐지 모델 상태."""
    return {
        "ospf_alerts":      ospf_monitor.recent_alerts(10),
        "traffic_trained":  security_detector._trained,
    }


@app.post("/reset-buffer")
def reset_buffer():
    global _support_buffer, _prev_obs, _prev_action
    _support_buffer, _prev_obs, _prev_action = [], None, 0
    return {"result": "buffer reset"}

@app.get("/live-results")
def live_results():
    """ZSM 실시간 결과 — summary.json 기반 + perf_tracker 실시간 MAML 성능 반영."""
    import json
    from pathlib import Path

    summary_path = Path(__file__).parent.parent / "experiments" / "results" / "summary.json"
    base: dict = {}
    if summary_path.exists():
        with open(summary_path, encoding="utf-8") as f:
            base = json.load(f)

    ms = perf_tracker.status()
    live_ttr = list(perf_tracker._ttr_list)
    if live_ttr:
        fewshot = dict(base.get("fewshot", {}))
        fewshot.update({
            "avg_ttr":    round(ms["avg_ttr"], 2),
            "ttr_list":   live_ttr,
            "avg_reward": ms["avg_reward"],
            "success_rate": round(
                sum(1 for t in live_ttr if t < 30) / len(live_ttr) * 100, 1
            ),
            "note": f"[실시간] steps={ms['steps']} sla_rate={ms['sla_viol_rate']:.2f}",
        })
        base["fewshot"] = fewshot

    base["_live"] = True
    base["_perf"] = ms
    return base


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("AI_ENGINE_PORT", 8000))
    uvicorn.run("api_server:app", host="0.0.0.0", port=port, reload=False)
