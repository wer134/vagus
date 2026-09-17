"""대시보드 데이터 생성기 (cowork/VISUALIZATION_PLAN.md §4 V-Phase 2).

대시보드는 `fetch()`를 쓰지 않는다 — `file://`로 열면 CORS가 막고, GitHub Pages에는
백엔드가 없다. 그래서 숫자를 페이지 안에 넣어야 하는데, **손으로 넣으면 안 된다.**
손으로 넣은 숫자는 손으로 고친 결과 파일과 같은 종류의 결함이다 (VISUALIZATION_PLAN §1-1).

이 스크립트가 `experiments/results/*.json`과 체크포인트 메타데이터를 읽어
`dashboard_data.js` (window.ANM_DATA)를 생성한다. 대시보드는 그것을 <script src>로 읽는다.
결과가 갱신되면 이 스크립트를 다시 돌린다 — 대시보드의 수치를 직접 편집하지 않는다.

    python3 experiments/make_dashboard_data.py

출력은 두 곳에 같은 내용으로 쓴다: 리포 루트(index.html 옆)와 autonomous-network-mgmt/
(dashboard.html 옆). 두 HTML이 바이트 단위로 같아야 하므로 데이터 파일도 같아야 한다.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)                 # autonomous-network-mgmt/
REPO = os.path.dirname(PROJECT)                 # 리포 루트
RESULTS = os.path.join(HERE, "results")
AGENTS = os.path.join(PROJECT, "ai-engine", "agents")

sys.path.insert(0, os.path.join(HERE, "figures"))
import palette  # noqa: E402  (figures/palette.py — 색 단일 출처)

AGENT_LABEL = {"ppo": "PPO (Baseline DRL)", "maml": "MAML (Few-shot)",
               "baseline": "PPO (Baseline DRL)", "fewshot": "MAML (Few-shot)"}
ABLATION_LABEL = {
    "analytics_only": "분석만 (규칙 기반 RCA)",
    "maml_only": "MAML만 (학습 정책 단독)",
    "combined": "결합 (분석 + MAML)",
}


def load(name, required=True):
    path = os.path.join(RESULTS, name)
    if not os.path.exists(path):
        if required:
            raise SystemExit(f"결과 파일이 없다: {path}\n  experiments/reproduce.sh를 먼저 돌릴 것")
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def provenance(doc):
    """결과 파일이 어디서 왔는지 — 커밋·계약 버전·측정 조건. 그림의 조건 스탬프와 같은 역할."""
    return {
        "git_commit": doc.get("git_commit"),
        "timestamp": doc.get("timestamp"),
        "seed": doc.get("seed"),
        "contract": doc.get("contract"),
        "condition": doc.get("_condition"),
    }


def histogram(values):
    """TTR 히스토그램. 구간을 임의로 묶지 않고 관측된 정수값을 그대로 쓴다."""
    c = Counter(int(v) for v in values)
    return sorted(c.items())


# ── 1) 폐쇄 루프 (stress_test) ────────────────────────────────────────────────
def closed_loop(doc):
    eps = doc["results"]
    test = [e["ttr"] for e in eps if e.get("group") == "test"]
    train = [e["ttr"] for e in eps if e.get("group") == "train"]
    bins = sorted({int(t) for t in test + train})
    return {
        "n": doc["total"],
        "avg_ttr": doc["avg_ttr"],
        "test_avg_ttr": doc.get("test_avg_ttr"),
        "train_avg_ttr": doc.get("train_avg_ttr"),
        "n_test": len(test),
        "n_train": len(train),
        "success_rate": doc["success_rate"],
        "rca_first_pct": doc["root_cause_accuracy_pct"],
        "rca_all_pct": doc["rca_all_cycles_ok_pct"],
        "wasted_per_ep": doc["wasted_actions_per_ep"],
        "hist_bins": bins,
        "hist_test": [sum(1 for t in test if t == b) for b in bins],
        "hist_train": [sum(1 for t in train if t == b) for b in bins],
        "provenance": provenance(doc),
    }


# ── 2) 절제 실험 ──────────────────────────────────────────────────────────────
def ablation(doc):
    order = ["analytics_only", "maml_only", "combined"]
    modes = []
    for key in order:
        if key not in doc["results"]:
            continue
        r = doc["results"][key]
        modes.append({
            "key": key,
            "label": ABLATION_LABEL.get(key, key),
            "avg_ttr": r["avg_ttr"],
            "success_rate": r["success_rate"],
            "wasted_per_ep": r["wasted_actions_per_ep"],
            "rca_all_pct": r.get("rca_all_cycles_ok_pct"),
        })
    return {"n_per_mode": doc["n_per_mode"], "modes": modes, "provenance": provenance(doc)}


# ── 3) 오프라인 평가 ──────────────────────────────────────────────────────────
def offline(doc, timeout=200.0):
    agents = []
    for key in ("baseline", "fewshot"):
        if key not in doc:
            continue
        a = doc[key]
        ttrs = a.get("ttr_list", [])
        solved = sum(1 for t in ttrs if t < timeout)
        agents.append({
            "key": key,
            "label": AGENT_LABEL[key],
            "avg_ttr": a["avg_ttr"],
            "success_rate": a["success_rate"],
            "avg_reward": a["avg_reward"],
            "n": len(ttrs),
            "solved": solved,
            "unsolved": len(ttrs) - solved,
            # 해결된 에피소드만의 평균 — 200(타임아웃)에 눌려 보이지 않는 값
            "avg_ttr_solved": round(sum(t for t in ttrs if t < timeout) / solved, 2) if solved else None,
        })
    return {
        "agents": agents,
        "timeout": timeout,
        "eval_links": doc.get("eval_links", []),
        "train_links": doc.get("train_links", []),
        "provenance": provenance(doc),
    }


# ── 4) 정책 붕괴 검사 ─────────────────────────────────────────────────────────
def policy(doc):
    agents = []
    threshold = None
    for key in ("ppo", "maml"):
        r = doc.get(key)
        if not (isinstance(r, dict) and r.get("loaded")):
            continue
        threshold = r.get("collapse_threshold", threshold)
        counts = r.get("on_policy_counts", {})
        total = sum(counts.values()) or 1
        agents.append({
            "key": key,
            "label": AGENT_LABEL[key],
            "collapsed": r["collapsed"],
            "top_action": r["top_action"],
            "top_share": r["top_action_share"],
            "entropy_bits": r["action_entropy_bits"],
            "distinct": r["distinct_actions"],
            "actions": [{"action": a, "count": n, "share": round(n / total, 4)}
                        for a, n in sorted(counts.items(), key=lambda kv: -kv[1])],
        })
    return {"agents": agents, "threshold": threshold, "provenance": provenance(doc)}


# ── 5) 학습 중 행동 분포 (T1) ─────────────────────────────────────────────────
def training(curves):
    out = {"algos": [], "threshold": None}
    for algo, doc in curves.items():
        if doc is None:
            continue
        total = doc["total"] or 1
        out["threshold"] = doc.get("collapse_threshold", out["threshold"])
        pts = [{
            "pct": round(100.0 * s["progress"] / total, 2),
            "progress": s["progress"],
            "entropy_bits": s["action_entropy_bits"],
            "top_share": s["top_action_share"],
            "collapsed": s["collapsed"],
        } for s in doc["samples"]]
        collapsed = [p for p in pts if p["collapsed"]]
        out["algos"].append({
            "key": algo,
            "label": AGENT_LABEL[algo],
            "total": doc["total"],
            "unit": "timesteps" if algo == "ppo" else "meta-iterations",
            "points": pts,
            "first_collapse_pct": collapsed[0]["pct"] if collapsed else None,
            "first_collapse_progress": collapsed[0]["progress"] if collapsed else None,
            "ends_collapsed": pts[-1]["collapsed"] if pts else None,
            "provenance": provenance(doc),
        })
    return out


# ── 6) 체크포인트 메타데이터 (T2) ─────────────────────────────────────────────
def checkpoints():
    rows = []
    if not os.path.isdir(AGENTS):
        return rows
    for fname in sorted(os.listdir(AGENTS)):
        if not fname.endswith(".meta.json") or "_pre_" in fname:
            continue
        with open(os.path.join(AGENTS, fname), encoding="utf-8") as f:
            m = json.load(f)
        pc = m.get("policy_check", {})
        tr = m.get("train", {})
        rows.append({
            "checkpoint": m.get("checkpoint", fname),
            "algo": tr.get("algo"),
            "git_commit": m.get("git_commit"),
            "timestamp": m.get("timestamp"),
            "seed": tr.get("seed"),
            "train_links": tr.get("train_links"),
            "collapsed": pc.get("collapsed"),
            "top_action": pc.get("top_action"),
            "top_share": pc.get("top_action_share"),
            "entropy_bits": pc.get("action_entropy_bits"),
            "distinct": pc.get("distinct_actions"),
        })
    return rows


# ── 7) 지속 버퍼 ──────────────────────────────────────────────────────────────
def persistent(doc):
    if doc is None:
        return None
    return {
        "n": doc["n_episodes"],
        "overall_avg_ttr": doc["overall_avg_ttr"],
        "early_avg_ttr": doc["early_avg_ttr"],
        "late_avg_ttr": doc["late_avg_ttr"],
        "early_adapt_rate": doc["early_adapt_rate"],
        "late_adapt_rate": doc["late_adapt_rate"],
        "success_rate": doc["success_rate"],
        "wasted_per_ep": doc["wasted_actions_per_ep"],
        "provenance": provenance(doc),
    }


def build(episodes=50):
    st = load(f"stress_{episodes}ep.json")
    abl = load("ablation_study.json")
    off = load("offline_eval.json")
    pc = load("policy_check.json")
    pb = load("persistent_buffer.json", required=False)
    curves = {"maml": load("train_curve_maml.json", required=False),
              "ppo": load("train_curve_ppo.json", required=False)}

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generator": "experiments/make_dashboard_data.py",
        "palette": {
            # 색은 figures/palette.py 한 곳에서만 나온다 — 대시보드가 자기 색을 고르지 않는다
            "categorical": palette.CATEGORICAL["dark"],
            "status": palette.STATUS,
            "deemphasis": palette.DEEMPHASIS["dark"],
        },
        "closed_loop": closed_loop(st),
        "ablation": ablation(abl),
        "offline": offline(off),
        "policy": policy(pc),
        "training": training(curves),
        "checkpoints": checkpoints(),
        "persistent": persistent(pb),
    }


def write(data, paths):
    body = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=False)
    text = (
        "// 자동 생성 — 손으로 고치지 말 것.\n"
        "// experiments/make_dashboard_data.py가 experiments/results/*.json에서 생성한다.\n"
        f"// 생성 시각: {data['generated_at']}\n"
        "window.ANM_DATA = " + body + ";\n"
    )
    for p in paths:
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"  {p}  ({len(text):,} bytes)")


def main():
    episodes = 50
    for arg in sys.argv[1:]:
        if arg.startswith("--episodes="):
            episodes = int(arg.split("=", 1)[1])
        else:
            raise SystemExit(f"알 수 없는 옵션: {arg}")

    data = build(episodes)
    write(data, [os.path.join(PROJECT, "dashboard_data.js"),
                 os.path.join(REPO, "dashboard_data.js")])

    cl, ab, pl = data["closed_loop"], data["ablation"], data["policy"]
    print(f"\n  폐쇄 루프 {cl['n']}ep: TTR {cl['avg_ttr']} (test {cl['test_avg_ttr']} / "
          f"train {cl['train_avg_ttr']})  성공 {cl['success_rate']}%")
    print(f"  절제 {len(ab['modes'])}종 · 체크포인트 {len(data['checkpoints'])}개 · "
          f"붕괴 {sum(1 for a in pl['agents'] if a['collapsed'])}/{len(pl['agents'])}")


if __name__ == "__main__":
    main()
