"""Mock SNMP REST API — Flask 기반, 포트 5001.

시뮬레이션 시계 (SIM_CLOCK 환경변수):
  lockstep (기본)  — 시간은 POST /debug/tick 요청으로만 진행한다. /auto-step이 사이클마다
                     1회 호출하므로 OODA 사이클 = 1틱. 실험 재현용.
  realtime:<ms>    — 백그라운드 스레드가 <ms>마다 tick. Java collector/대시보드처럼
                     주기적으로 관측만 하는 클라이언트를 위한 데모 모드.
GET /metrics 계열은 순수 조회이며 시간을 진행시키지 않는다 (cowork/AUDIT_2026-09-09.md P1).
"""
import os
import threading
import urllib.request
import json as _json
from flask import Flask, jsonify, request, abort
from metric_generator import (
    get_all_metrics,
    get_node_metrics,
    get_ospf_costs,
    get_node_stress,
    get_link_stress,
    get_congested_links,
    get_security_metrics,
    get_attack_state,
    get_tick,
    set_ospf_cost,
    inject_congestion,
    clear_congestion,
    inject_attack,
    clear_attack,
    reset_state,
    tick,
    NODES,
)

app = Flask(__name__)

SIM_CLOCK = os.environ.get("SIM_CLOCK", "lockstep")


def _start_realtime_clock(spec: str) -> None:
    """SIM_CLOCK=realtime:<ms> → 주기적 tick 스레드."""
    try:
        interval_ms = int(spec.split(":", 1)[1])
    except (IndexError, ValueError):
        raise SystemExit(f"SIM_CLOCK 형식 오류: {spec!r} (예: realtime:1000)")

    def _loop():
        import time as _t
        while True:
            _t.sleep(interval_ms / 1000.0)
            tick()

    threading.Thread(target=_loop, daemon=True, name="sim-clock").start()

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def handle_options(path):
    return "", 204


@app.route("/health")
def health():
    return jsonify({"status": "ok", "sim_clock": SIM_CLOCK, "tick": get_tick()})


# ── 메트릭 엔드포인트 ──────────────────────────────────────

@app.route("/metrics")
def metrics_all():
    """전체 노드 메트릭 반환."""
    return jsonify(get_all_metrics())


@app.route("/metrics/<node_id>")
def metrics_node(node_id: str):
    if node_id not in NODES:
        abort(404, description=f"Unknown node: {node_id}")
    return jsonify(get_node_metrics(node_id))


# ── OSPF 코스트 관리 ───────────────────────────────────────

@app.route("/ospf/costs")
def ospf_costs():
    return jsonify(get_ospf_costs())


@app.route("/ospf/costs/<link>", methods=["PUT"])
def update_ospf_cost(link: str):
    """링크 OSPF 코스트 변경.

    PUT /ospf/costs/r1-r2
    Body: {"cost": 50}
    """
    body = request.get_json(force=True, silent=True) or {}
    cost = body.get("cost")
    if cost is None or not isinstance(cost, int) or cost not in (10, 20, 50, 100, 200):
        abort(400, description="cost must be one of [10, 20, 50, 100, 200]")
    if not set_ospf_cost(link, cost):
        abort(404, description=f"Unknown link: {link}")
    return jsonify({"link": link, "cost": cost, "result": "ok"})


# ── 혼잡 주입 (테스트용) ────────────────────────────────────

@app.route("/debug/congestion/<link>", methods=["POST"])
def inject(link: str):
    if not inject_congestion(link):
        abort(404)
    return jsonify({"link": link, "congested": True})


@app.route("/debug/congestion/<link>", methods=["DELETE"])
def clear(link: str):
    clear_congestion(link)
    return jsonify({"link": link, "congested": False})


@app.route("/debug/reset", methods=["POST"])
def reset():
    """에피소드 리셋: 스트레스·혼잡·OSPF cost·틱 초기화.

    Body(선택): {"seed": 42} — 노이즈 난수원을 고정해 실험을 재현 가능하게 한다.
    """
    body = request.get_json(force=True, silent=True) or {}
    seed = body.get("seed")
    reset_state(seed=int(seed) if seed is not None else None)
    return jsonify({"result": "reset", "seed": seed, "tick": get_tick()})


@app.route("/debug/tick", methods=["POST"])
def debug_tick():
    """시뮬레이션 시간을 n스텝 진행 (기본 1). lockstep 모드에서 시간을 진행시키는 유일한 수단."""
    body = request.get_json(force=True, silent=True) or {}
    n = int(body.get("n", 1))
    return jsonify({"tick": tick(n)})


# ── 공격 주입 (보안 테스트용) ──────────────────────────────────

@app.route("/debug/attack/<attack_type>", methods=["POST"])
def start_attack(attack_type: str):
    """보안 공격 시뮬레이션 시작 (attack_type: 'ddos' | 'portscan')."""
    if not inject_attack(attack_type):
        abort(400, description="attack_type must be 'ddos' or 'portscan'")
    return jsonify({"attack": attack_type, "active": True})


@app.route("/debug/attack", methods=["DELETE"])
def stop_attack():
    """공격 시뮬레이션 중지."""
    clear_attack()
    return jsonify({"attack": None, "active": False})


@app.route("/metrics/security")
def security_metrics_all():
    """보안 피처 포함 전체 노드 메트릭 반환."""
    return jsonify([get_security_metrics(n) for n in NODES])


@app.route("/metrics/security/<node_id>")
def security_metrics_node(node_id: str):
    if node_id not in NODES:
        abort(404, description=f"Unknown node: {node_id}")
    return jsonify(get_security_metrics(node_id))


@app.route("/debug/attack-state")
def attack_state():
    state = get_attack_state()
    return jsonify({"attack": state, "active": state is not None})


@app.route("/debug/stress")
def stress():
    return jsonify(get_node_stress())


@app.route("/debug/state")
def debug_state():
    """시뮬레이터 내부 상태 (틱, 링크 스트레스, 혼잡 링크, cost)."""
    return jsonify({
        "tick":            get_tick(),
        "sim_clock":       SIM_CLOCK,
        "link_stress":     get_link_stress(),
        "congested_links": get_congested_links(),
        "ospf_costs":      get_ospf_costs(),
        "attack":          get_attack_state(),
    })


# ── AI Engine 프록시 (CORS 우회용) ────────────────────────────────────────────
AI_ENGINE_URL = os.environ.get("AI_ENGINE_URL", "http://localhost:8000")

@app.route("/ai/<path:path>", methods=["GET", "POST"])
def ai_proxy(path: str):
    """브라우저 → 포트 5001 → AI Engine 포트 8000 프록시."""
    target = f"{AI_ENGINE_URL}/{path}"
    body   = request.get_data()
    headers = {"Content-Type": "application/json"}
    try:
        req  = urllib.request.Request(target, data=body or None, headers=headers, method=request.method)
        resp = urllib.request.urlopen(req, timeout=10)
        return app.response_class(resp.read(), status=resp.status, mimetype="application/json")
    except Exception as e:
        return jsonify({"error": str(e)}), 502


if __name__ == "__main__":
    port = int(os.environ.get("FLASK_PORT", 5001))
    if SIM_CLOCK.startswith("realtime"):
        _start_realtime_clock(SIM_CLOCK)
    print(f"[mock_snmp_agent] SIM_CLOCK={SIM_CLOCK}  port={port}", flush=True)
    app.run(host="0.0.0.0", port=port, threaded=True)
