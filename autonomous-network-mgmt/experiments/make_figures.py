"""결과 JSON → 도판 (cowork/VISUALIZATION_PLAN.md).

손으로 숫자를 넣은 그림은 손으로 고친 결과 파일과 같은 결함이다. 모든 도판은 여기서
`results/*.json`을 읽어 생성하고, `figspec.save()`가 측정 조건(commit·seed·계약 버전)을
그림에 찍는다. `reproduce.sh`의 마지막 단계로 편입돼 결과와 그림이 함께 갱신된다.

    python experiments/make_figures.py            # 전부
    python experiments/make_figures.py T1 F2      # 일부만

구현된 도판 (우선순위 순 — VISUALIZATION_PLAN §3 우선순위 표)
    T1  train_collapse      학습 중 붕괴 곡선     — 열린 질문(P8)에 답한다
    T2  checkpoint_history  체크포인트 이력       — Track A 스코어보드
    F2  ablation_pair       절제: TTR vs 부수피해 — 이중 축 금지, 패널 2개
    F3  policy_actions      정책 행동 분포        — 붕괴가 한눈에
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "figures"))

import figspec  # noqa: E402
import palette  # noqa: E402

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
AGENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ai-engine", "agents")
INK = palette.INK["light"]


def load(name: str) -> dict | None:
    path = os.path.join(RESULTS, name)
    if not os.path.exists(path):
        print(f"  건너뜀 — {name} 없음", flush=True)
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── T1. 학습 중 붕괴 곡선 ─────────────────────────────────────────────────────

def fig_train_collapse() -> bool:
    """정책이 **언제** 탐색을 그만두는가. 손실이 아니라 행동을 그린다.

    패널 2개(엔트로피 / 최빈 행동 비율) — 스케일이 다르므로 이중 축을 쓰지 않는다.
    x축은 학습 진행률(%)로 정규화해 iteration(MAML)과 timestep(PPO)을 나란히 놓는다.
    """
    curves = {k: load(f"train_curve_{k}.json") for k in ("maml", "ppo")}
    curves = {k: v for k, v in curves.items() if v and v.get("samples")}
    if not curves:
        return False

    colors = dict(zip(curves, palette.categorical(len(curves))))
    fig, (ax_e, ax_s) = figspec.new_figure(2, 1, figsize=(9, 6.2), sharex=True)

    for algo, doc in curves.items():
        xs = [s["progress"] / doc["total"] * 100 for s in doc["samples"]]
        ent = [s["action_entropy_bits"] for s in doc["samples"]]
        share = [s["top_action_share"] for s in doc["samples"]]
        ax_e.plot(xs, ent, color=colors[algo], linewidth=2,
                  marker="o", markersize=4, label=algo.upper())
        ax_s.plot(xs, share, color=colors[algo], linewidth=2,
                  marker="o", markersize=4, label=algo.upper())
        # 직접 라벨 — 색만으로 정체성을 주지 않는다
        ax_e.annotate(algo.upper(), (xs[-1], ent[-1]), textcoords="offset points",
                      xytext=(6, 0), color=INK["primary"], fontsize=9, va="center")

    thr = next(iter(curves.values())).get("collapse_threshold", 0.8)
    ax_s.axhline(thr, color=INK["axis"], linestyle="--", linewidth=1.5)
    ax_s.annotate(f"collapse threshold {thr}", (2, thr), textcoords="offset points",
                  xytext=(0, 5), color=INK["muted"], fontsize=8)

    ax_e.set_ylabel("action entropy (bits)")
    ax_e.set_title("Does the policy keep exploring during training?",
                   fontsize=11, loc="left", pad=10)
    ax_e.legend(frameon=False, fontsize=9, labelcolor=INK["secondary"])
    ax_s.set_ylabel("top-action share")
    ax_s.set_xlabel("training progress (%)")
    ax_s.set_ylim(0, 1.05)
    for ax in (ax_e, ax_s):
        ax.grid(axis="y", color=INK["axis"], linewidth=0.6, alpha=0.5)
        ax.set_axisbelow(True)

    src = next(iter(curves.values()))
    figspec.save(fig, "T1_train_collapse", source_doc=src,
                 condition="probe: entropy/top-share, not loss")
    return True


# ── T2. 체크포인트 이력 ───────────────────────────────────────────────────────

def fig_checkpoint_history() -> bool:
    """재학습마다 붕괴 지표가 어떻게 움직였나 — Track A의 스코어보드.

    데이터는 이미 있다: 학습이 끝날 때마다 `<체크포인트>.meta.json`이 남는다.
    """
    metas = []
    for path in sorted(glob.glob(os.path.join(AGENTS, "*.meta.json"))):
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        pc = m.get("policy_check") or {}
        if not pc:
            continue
        metas.append({
            "name": os.path.basename(path).replace(".meta.json", ""),
            "algo": (m.get("train") or {}).get("algo", "?"),
            "sim": (m.get("contract") or {}).get("sim_version", "?"),
            "entropy": pc.get("action_entropy_bits", 0.0),
            "share": pc.get("top_action_share", 1.0),
            "distinct": pc.get("distinct_actions", 1),
            "collapsed": pc.get("collapsed", True),
        })
    if not metas:
        return False
    metas.sort(key=lambda m: (str(m["sim"]), m["name"]))

    labels = [f"{m['name'].replace('_network', '')}\n(sim{m['sim']})" for m in metas]
    figspec.assert_ascii(*labels)
    fig, (ax_e, ax_d) = figspec.new_figure(1, 2, figsize=(11, 4.4))

    # 붕괴 여부는 색이 아니라 마커+라벨로도 읽히게 (상태색은 계열색으로 재사용하지 않는다)
    face = [palette.STATUS["critical"] if m["collapsed"] else palette.STATUS["good"]
            for m in metas]
    x = range(len(metas))
    ax_e.bar(x, [m["entropy"] for m in metas], color=face, width=0.55)
    for i, m in enumerate(metas):
        ax_e.text(i, m["entropy"] + 0.05, f"{m['entropy']:.2f}", ha="center",
                  fontsize=9, color=INK["primary"])
        ax_e.text(i, -0.13, "collapsed" if m["collapsed"] else "ok", ha="center",
                  fontsize=8, color=INK["muted"])
    ax_e.set_ylabel("action entropy (bits)")
    ax_e.set_title("Checkpoint history: exploration", fontsize=11, loc="left", pad=10)
    ax_e.set_ylim(-0.2, max(m["entropy"] for m in metas) * 1.3 + 0.3)

    # 패널이 달라도 같은 체크포인트는 같은 색 — 색은 대상을 따른다.
    # 상태색이므로 막대 아래 "collapsed"/"ok" 라벨과 항상 함께 읽힌다 (색 단독 금지).
    ax_d.bar(x, [m["distinct"] for m in metas], color=face, width=0.55)
    for i, m in enumerate(metas):
        ax_d.text(i, m["distinct"] + 0.1, str(m["distinct"]), ha="center",
                  fontsize=9, color=INK["primary"])
    ax_d.set_ylabel("distinct actions")
    ax_d.set_title("Checkpoint history: action variety", fontsize=11, loc="left", pad=10)

    for ax in (ax_e, ax_d):
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, fontsize=8)
        ax.grid(axis="y", color=INK["axis"], linewidth=0.6, alpha=0.5)
        ax.set_axisbelow(True)

    figspec.save(fig, "T2_checkpoint_history", source_doc=None,
                 condition=f"{len(metas)} checkpoints from ai-engine/agents/*.meta.json")
    return True


# ── F2. 절제 — TTR은 같고 부수 피해만 늘어난다 ────────────────────────────────

def fig_ablation_pair() -> bool:
    """TTR과 부수 피해는 스케일이 다르다 — **이중 축 대신 패널 두 개.**"""
    doc = load("ablation_study.json")
    if not doc:
        return False
    order = ["analytics_only", "maml_only", "combined"]
    modes = [m for m in order if m in doc["results"]]
    ttr = [doc["results"][m]["avg_ttr"] for m in modes]
    waste = [doc["results"][m]["wasted_actions_per_ep"] for m in modes]
    labels = [m.replace("_", " ") for m in modes]
    figspec.assert_ascii(*labels)

    fig, (ax_t, ax_w) = figspec.new_figure(1, 2, figsize=(10.5, 3.6))
    y = list(range(len(modes)))[::-1]

    b1 = ax_t.barh(y, ttr, color=palette.SEQUENTIAL_DEFAULT, height=0.55)
    figspec.label_bars(ax_t, b1, ttr, "{:.2f}")
    ax_t.set_title("Avg TTR (OODA cycles) - lower is better",
                   fontsize=10.5, loc="left", pad=10)
    ax_t.set_xlim(0, max(ttr) * 1.22)

    b2 = ax_w.barh(y, waste, color=palette.SEQUENTIAL_BLUE[300], height=0.55)
    figspec.label_bars(ax_w, b2, waste, "{:.2f}")
    ax_w.set_title("Wasted actions per episode - lower is better",
                   fontsize=10.5, loc="left", pad=10)
    ax_w.set_xlim(0, max(max(waste) * 1.25, 0.1))

    for ax in (ax_t, ax_w):
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=9.5)
        ax.grid(axis="x", color=INK["axis"], linewidth=0.6, alpha=0.5)
        ax.set_axisbelow(True)

    figspec.save(fig, "F2_ablation_pair", source_doc=doc,
                 condition=f"{doc.get('n_per_mode', '?')} ep/mode")
    return True


# ── F3. 정책 행동 분포 ────────────────────────────────────────────────────────

def fig_policy_actions() -> bool:
    """붕괴가 한눈에 — 한 덩어리 막대 vs 조각난 막대."""
    doc = load("policy_check.json")
    if not doc:
        return False
    agents = {k: v for k, v in doc.items() if isinstance(v, dict) and v.get("loaded")}
    if not agents:
        return False

    # 행동 색은 범주 팔레트 고정 순서. 상한(8)을 넘으면 꼬리를 'other'로 접는다.
    seen: list[str] = []
    for a in agents.values():
        for act in a["on_policy_counts"]:
            if act not in seen:
                seen.append(act)
    cap = len(palette.CATEGORICAL["light"]) - 1
    keep = seen[:cap]
    colors = dict(zip(keep, palette.categorical(len(keep))))
    other_color = palette.DEEMPHASIS["light"]
    figspec.assert_ascii(*keep, *agents)

    fig, ax = figspec.new_figure(figsize=(10, 3.4))
    names = list(agents)
    for row, name in enumerate(names):
        counts = agents[name]["on_policy_counts"]
        total = sum(counts.values()) or 1
        left = 0.0
        for act in keep + ["other"]:
            n = counts.get(act, 0) if act != "other" else sum(
                v for k, v in counts.items() if k not in keep)
            if not n:
                continue
            frac = n / total * 100
            ax.barh(row, frac, left=left, height=0.5,
                    color=colors.get(act, other_color),
                    edgecolor=palette.SURFACE["light"], linewidth=2)  # 2px 표면 간격
            if frac >= 12:   # 큰 조각만 직접 라벨 (대비 WARN 해소 조건)
                ax.text(left + frac / 2, row, f"{act}\n{frac:.0f}%", ha="center",
                        va="center", fontsize=8, color=INK["primary"])
            left += frac
        a = agents[name]
        n_act = a["distinct_actions"]
        ax.text(101, row, f"{a['action_entropy_bits']:.2f} bit / "
                          f"{n_act} action{'s' if n_act != 1 else ''}",
                va="center", fontsize=9, color=INK["secondary"])

    ax.set_yticks(range(len(names)))
    ax.set_yticklabels([n.upper() for n in names], fontsize=10)
    ax.set_xlim(0, 100)
    ax.set_xlabel("share of actions chosen (%)")
    ax.set_title("What does each policy actually do? (congestion on each link, 8 steps)",
                 fontsize=11, loc="left", pad=10)
    handles = [__import__("matplotlib").patches.Patch(color=colors[a], label=a) for a in keep]
    ax.legend(handles=handles, frameon=False, fontsize=8, ncol=min(len(keep), 4),
              loc="upper center", bbox_to_anchor=(0.5, -0.28),
              labelcolor=INK["secondary"])
    figspec.save(fig, "F3_policy_actions", source_doc=doc)
    return True


FIGURES = {
    "T1": ("train_collapse", fig_train_collapse),
    "T2": ("checkpoint_history", fig_checkpoint_history),
    "F2": ("ablation_pair", fig_ablation_pair),
    "F3": ("policy_actions", fig_policy_actions),
}


def main() -> int:
    wanted = [a.upper() for a in sys.argv[1:]] or list(FIGURES)
    unknown = [w for w in wanted if w not in FIGURES]
    if unknown:
        print(f"알 수 없는 도판: {unknown}. 가능: {list(FIGURES)}", file=sys.stderr)
        return 2
    made, skipped = [], []
    for key in wanted:
        name, fn = FIGURES[key]
        print(f"[{key}] {name}", flush=True)
        (made if fn() else skipped).append(key)
    print(f"\n생성 {len(made)}개 {made}" + (f"  ·  건너뜀 {skipped}" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
