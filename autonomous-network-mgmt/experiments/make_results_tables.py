"""문서의 결과 표를 결과 파일에서 생성·검사한다 (2026-11-20).

이 프로젝트는 문서 수치와 실제 측정이 어긋난 이력이 있다 (TTR 3.78, "98.1 % 단축" — 둘 다 철회).
그래서 cowork/RESULTS.md와 README.md의 결과 표는 손으로 쓰지 않는다. 문서 안의 표시 구간

    <!-- AUTO:이름 -->
    ...
    <!-- /AUTO:이름 -->

을 이 스크립트가 results/*.json에서 만든 표로 채운다.

  python3 experiments/make_results_tables.py --write   # 표 갱신
  python3 experiments/make_results_tables.py --check   # 문서 표가 결과 파일과 다르면 실패 (selftest.sh)
"""
import json
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).parent
RES = HERE / "results"
DOCS = [HERE.parent / "cowork" / "RESULTS.md", HERE.parent.parent / "README.md"]


def load(name: str) -> dict:
    with open(RES / name, encoding="utf-8") as f:
        return json.load(f)


def pct(a: float, b: float) -> str:
    return f"{100 * a / b:.1f} %"


def src(*names: str) -> str:
    return "출처: " + ", ".join(f"`results/{n}` (커밋 `{load(n)['git_commit']}`)" for n in names)


# ── 실험 1: K4 단일 혼잡 절제 ─────────────────────────────────────────────────

def t_ablation() -> str:
    a = load("ablation_study.json")
    s = load("stress_50ep.json")
    rows = ["| 구성 | 평균 TTR (사이클) | 성공률 | 정상 링크 cost 변경/ep |", "| --- | --- | --- | --- |"]
    names = {"analytics_only": "규칙(Analytics) 단독", "combined": "규칙 + MAML", "maml_only": "MAML 단독"}
    for k in ("analytics_only", "combined", "maml_only"):
        r = a["results"][k]
        rows.append(f"| {names[k]} | {r['avg_ttr']:.2f} | {r['success_rate']:.0f} % | {r['wasted_actions_per_ep']:.2f} |")
    note = (f"\n규칙 + MAML 폐쇄 루프 스트레스 시험 50 ep: 평균 TTR {s['avg_ttr']:.2f} "
            f"(TEST 링크 {s['test_avg_ttr']:.2f} / TRAIN 링크 {s['train_avg_ttr']:.2f}), "
            f"성공률 {s['success_rate']:.0f} %, 근본 원인 첫 진단 정확도 {s['root_cause_accuracy_pct']:.0f} %.\n\n"
            + src("ablation_study.json", "stress_50ep.json"))
    return "\n".join(rows) + "\n" + note


# ── 실험 2: K4 용량·이중 혼잡 — 기준선 사다리 ─────────────────────────────────

def _cap_rows():
    v1, v2, v3 = (load(f) for f in ("capacity_probe.json", "capacity_probe_v2.json", "capacity_probe_v3.json"))
    key = lambda r: (tuple(r["congested"]), r["profile"])
    solv = {key(r): r for r in v1["scenarios"] if r["oracle"]["solvable"]}
    idx = {1: {key(r): r for r in v1["scenarios"]}, 2: {key(r): r for r in v2["scenarios"]},
           3: {key(r): r for r in v3["scenarios"]}}
    assert set(solv) == set(idx[2]) == set(idx[3]), "v2/v3는 풀이 있는 시나리오만 돌렸어야 한다"
    return solv, idx


def t_capacity() -> str:
    solv, idx = _cap_rows()
    n = len(solv)
    rows = ["| Analytics | 진단 | 조치 | 시나리오 완전 해결 | 에피소드 안정 복구 |", "| --- | --- | --- | --- | --- |"]
    desc = {1: ("노드 지표로 링크 추정", "원인 링크 cost 100"),
            2: ("링크를 직접 관측", "고장 링크만 cost 100"),
            3: ("v2와 같음", "라우팅 모델로 cost 배치 15,625개 전수 탐색")}
    for v in (1, 2, 3):
        rs = [idx[v][k]["rule"] for k in solv]
        full = sum(r["stable_rate"] == 1.0 for r in rs)
        eps = [e for r in rs for e in r["episodes"]]
        st_ = sum(e["stable"] for e in eps)
        rows.append(f"| v{v} | {desc[v][0]} | {desc[v][1]} | {full} / {n} | {st_} / {len(eps)} |")
    return "\n".join(rows) + "\n\n" + src("capacity_probe.json", "capacity_probe_v2.json", "capacity_probe_v3.json")


def t_capacity_segments() -> str:
    solv, idx = _cap_rows()
    segs = [
        ("단일 혼잡 · 균일 용량", lambda r: len(r["congested"]) == 1 and r["profile"] == "uniform"),
        ("단일 혼잡 · 약한 링크가 우회 경로 밖", lambda r: len(r["congested"]) == 1 and r["profile"] != "uniform"
         and r["oracle"]["min_changes"] == 1),
        ("단일 혼잡 · 약한 링크가 우회 경로 위", lambda r: len(r["congested"]) == 1 and r["profile"] != "uniform"
         and r["oracle"]["min_changes"] > 1),
        ("서로 떨어진 이중 혼잡", lambda r: len(r["congested"]) == 2),
    ]
    rows = ["| 구간 (시나리오 수) | v1 | v2 | v3 |", "| --- | --- | --- | --- |"]
    total = 0
    for name, sel in segs:
        keys = [k for k, r in solv.items() if sel(r)]
        total += len(keys)
        cells = []
        for v in (1, 2, 3):
            eps = [e for k in keys for e in idx[v][k]["rule"]["episodes"]]
            cells.append(f"{sum(e['stable'] for e in eps)}/{len(eps)}")
        rows.append(f"| {name} ({len(keys)}) | " + " | ".join(cells) + " |")
    assert total == len(solv), "구간이 시나리오 전체를 덮지 않는다"
    return "\n".join(rows)


# ── 실험 3: K4 수요 변화 ──────────────────────────────────────────────────────

def t_demand_shift() -> str:
    L = {v: load(f"demand_shift_v{v}.json") for v in (2, 3, 4)}
    key = lambda r: (tuple(r["congested"]), r["profile"], r["shift"]["pair"])
    base = L[4]["scenarios"]
    for v in (2, 3):
        assert [key(r) for r in L[v]["scenarios"]] == [key(r) for r in base]
    idx = {v: {key(r): r for r in L[v]["scenarios"]} for v in L}

    def detour(r):   # 기반 시나리오에서 약한 링크가 우회 경로 위 (규칙 v2로 못 푸는 기반)
        if r["profile"] == "uniform":
            return False
        w, c = r["profile"].split(":")[1], r["congested"][0]
        return w != c and bool(set(w.split("-")) & set(c.split("-")))

    segs = [
        ("수요 변화가 원래 계획을 깬다", lambda r: not r["stale_plan_survives"]),
        ("원래 계획 유지 · 기반이 규칙으로 풀림", lambda r: r["stale_plan_survives"] and not detour(r)),
        ("원래 계획 유지 · 기반에 계획기 필요", lambda r: r["stale_plan_survives"] and detour(r)),
        ("**합계**", lambda r: True),
    ]
    rows = ["| 구간 (시나리오 / 에피소드) | v2 규칙 | v3 고정 계획기 | v4 적응형 계획기 |", "| --- | --- | --- | --- |"]
    for name, sel in segs:
        keys = [key(r) for r in base if sel(r)]
        n_ep = sum(len(idx[4][k]["episodes"]) for k in keys)
        cells = [str(sum(e["stable"] for k in keys for e in idx[v][k]["episodes"])) for v in (2, 3, 4)]
        if name == "**합계**":
            cells = [f"**{c}**" for c in cells]
        rows.append(f"| {name} ({len(keys)} / {n_ep}) | " + " | ".join(cells) + " |")
    return "\n".join(rows) + "\n\n" + src("demand_shift_v2.json", "demand_shift_v3.json", "demand_shift_v4.json")


# ── 실험 4: Abilene 규모 확장 ─────────────────────────────────────────────────

def _scale():
    A, B = load("scale_benchmark.json"), load("scale_benchmark_ppo_r3.json")
    assert [e["key"] for e in A["episodes"]] == [e["key"] for e in B["episodes"]]
    p2 = [k for k in A["summary"]["all"] if k.startswith("ppo:")]
    p3 = [k for k in B["summary"]["all"] if k.startswith("ppo:")]
    return A, B, p2, p3


def t_scale() -> str:
    A, B, p2, p3 = _scale()
    S, T = A["summary"], B["summary"]
    n = S["solvable"]["n"]
    nsh, ndb = S["solvable/shift"]["n"], S["solvable/double"]["n"]
    rows = [f"| 제어기 | 안정 복구 (풀이 있는 {n}) | 비율 | 수요 변화 있음 ({nsh}) | 이중 혼잡 ({ndb}) | 결정 시간 |",
            "| --- | --- | --- | --- | --- | --- |"]
    names = {"noop": "무행동", "rule_v2": "규칙 v2", "planner_true": "계획기 (참 수요)",
             "planner_estimated": "계획기 (추정 수요)"}
    for k in ("noop", "rule_v2", "planner_true", "planner_estimated"):
        v = S["solvable"][k]
        ms = "—" if k == "noop" else f"{S['all'][k]['decide_ms_mean']:.3g} ms"
        rows.append(f"| {names[k]} | {v['stable']} | {pct(v['stable'], n)} | {S['solvable/shift'][k]['stable']} "
                    f"| {S['solvable/double'][k]['stable']} | {ms} |")
    for label, summ, keys in (("PPO 사전 등록 (보상 v2), seed 0–4", S, p2),
                              ("PPO 사후 변형 (보상 v3, 풀이 있는 시나리오만), seed 0–4", T, p3)):
        per = [summ["solvable"][k]["stable"] for k in keys]
        m, sd = st.mean(per), st.stdev(per)
        sh = st.mean(summ["solvable/shift"][k]["stable"] for k in keys)
        db = st.mean(summ["solvable/double"][k]["stable"] for k in keys)
        ms = st.mean(summ["all"][k]["decide_ms_mean"] for k in keys)
        rows.append(f"| {label} | {' / '.join(map(str, per))} | {100 * m / n:.1f} % ± {100 * sd / n:.1f} "
                    f"| {sh:.1f} | {db:.1f} | {ms:.2g} ms |")
    return "\n".join(rows) + "\n\n" + src("scale_benchmark.json", "scale_benchmark_ppo_r3.json")


def t_scale_overlap() -> str:
    A, B, p2, p3 = _scale()
    ea, eb = A["episodes"], B["episodes"]
    sol = [i for i, e in enumerate(ea) if e["oracle"]["solvable"]]
    plan = {i for i in sol if ea[i]["results"]["planner_estimated"]["stable"]}
    rule = {i for i in sol if ea[i]["results"]["rule_v2"]["stable"]}
    any3 = {i for i in sol if any(eb[i]["results"][k]["stable"] for k in p3)}
    maj3 = {i for i in sol if sum(eb[i]["results"][k]["stable"] for k in p3) >= 3}
    miss = sorted(set(sol) - plan)
    return "\n".join([
        "| 에피소드 단위 비교 (풀이 있는 에피소드) | 수 |", "| --- | --- |",
        f"| 계획기(추정 수요)가 놓친 에피소드 | {len(miss)} |",
        f"| 그중 PPO 사후 변형 seed 하나라도 푼 것 | {len(set(miss) & any3)} |",
        f"| 규칙이 푼 에피소드 | {len(rule)} |",
        f"| 그중 PPO 사후 변형 seed 과반(≥3)이 푼 것 | {len(rule & maj3)} |",
        f"| 규칙은 못 풀고 PPO 사후 변형 seed 하나라도 푼 것 | {len(any3 - rule)} |",
    ])


# ── README 머리 표 ────────────────────────────────────────────────────────────

def t_headline() -> str:
    a = load("ablation_study.json")["results"]
    solv, idx = _cap_rows()
    capfull = {v: sum(idx[v][k]["rule"]["stable_rate"] == 1.0 for k in solv) for v in (1, 2, 3)}
    D = {v: load(f"demand_shift_v{v}.json")["summary"]["all"] for v in (2, 3, 4)}
    A, B, p2, p3 = _scale()
    S, T = A["summary"]["solvable"], B["summary"]["solvable"]
    n = S["n"]
    m2 = st.mean(S[k]["stable"] for k in p2)
    m3 = st.mean(T[k]["stable"] for k in p3)
    return "\n".join([
        "| 실험 | 모델 없는 규칙 | 모델 기반 계획기 | 학습 정책 |",
        "| --- | --- | --- | --- |",
        f"| 4노드 단일 혼잡 (50 ep) | 성공 {a['analytics_only']['success_rate']:.0f} %, "
        f"TTR {a['analytics_only']['avg_ttr']:.2f} | — | MAML 단독 {a['maml_only']['success_rate']:.0f} % |",
        f"| 4노드 용량·이중 혼잡 (풀이 있는 {len(solv)} 시나리오) | v1 {capfull[1]}, v2 {capfull[2]} | {capfull[3]} | — |",
        f"| 4노드 수요 변화 ({D[4]['episodes']} ep) | {D[2]['episodes_stable']} | 고정 {D[3]['episodes_stable']} · "
        f"적응형 {D[4]['episodes_stable']} | — |",
        f"| Abilene (풀이 있는 {n} ep) | {S['rule_v2']['stable']} | {S['planner_estimated']['stable']} "
        f"| PPO {m2:.1f} (사전 등록) · {m3:.1f} (사후 변형) |",
    ])


TABLES = {
    "headline": t_headline, "ablation": t_ablation, "capacity": t_capacity,
    "capacity_segments": t_capacity_segments, "demand_shift": t_demand_shift,
    "scale": t_scale, "scale_overlap": t_scale_overlap,
}


def render(text: str) -> tuple[str, list[str]]:
    used = []
    for name, fn in TABLES.items():
        start, end = f"<!-- AUTO:{name} -->", f"<!-- /AUTO:{name} -->"
        while start in text:
            i = text.index(start) + len(start)
            j = text.index(end, i)
            text = text[:i] + "\n" + fn() + "\n" + text[j:]
            used.append(name)
            if text.count(start) == 1:
                break
    return text, used


def main(mode: str) -> int:
    bad = 0
    for doc in DOCS:
        old = doc.read_text(encoding="utf-8")
        new, used = render(old)
        rel = doc.relative_to(HERE.parent.parent)
        if mode == "--write":
            doc.write_text(new, encoding="utf-8")
            print(f"{rel}: 표 {len(used)}개 갱신 ({', '.join(used) or '없음'})")
        elif new != old:
            bad += 1
            print(f"FAIL — {rel}: 결과 파일과 다른 표가 있다. --write로 다시 생성할 것")
        else:
            print(f"OK — {rel}: 표 {len(used)}개가 결과 파일과 일치")
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("--write", "--check"):
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
