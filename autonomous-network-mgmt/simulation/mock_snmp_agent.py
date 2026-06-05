"""Mock SNMP REST API — Flask 기반, 포트 5001."""
import os
import urllib.request
import json as _json
from flask import Flask, jsonify, request, abort
from metric_generator import (
    get_all_metrics,
    get_node_metrics,
    get_ospf_costs,
    get_node_stress,
    set_ospf_cost,
    inject_congestion,
    clear_congestion,
    reset_state,
    NODES,
)

app = Flask(__name__)

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
    return jsonify({"status": "ok"})


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
    """에피소드 리셋: 스트레스·혼잡·OSPF cost 초기화."""
    reset_state()
    return jsonify({"result": "reset"})


@app.route("/debug/stress")
def stress():
    return jsonify(get_node_stress())


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
    app.run(host="0.0.0.0", port=port, threaded=True)
