"""Analytics-only 폐쇄 루프를 HTTP 없이 재현 (개정 후 버전).

metric_generator(tick 분리) + anomaly_detector.root_cause_analysis()를 직접 구동해
사이클당 1틱 기준 TTR과 정상 링크 cost 변경 횟수(wasted)를 잰다.
실행: python cowork/scratch/rca_trace.py   (numpy/sklearn 필요)
"""
import sys, os, random
ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "simulation")); sys.path.insert(0, os.path.join(ROOT, "ai-engine"))
import metric_generator as mg
from anomaly_detector import diagnose, root_cause_analysis

def episode(link, seed):
    mg.reset_state(seed=seed); mg.inject_congestion(link)
    actions = []
    for step in range(1, 16):
        mg.tick()                                   # /auto-step: 사이클당 1틱
        metrics = mg.get_all_metrics(); ospf = mg.get_ospf_costs()
        d = diagnose(metrics, ospf); rc = root_cause_analysis(d, ospf)
        if d["anomaly_detected"] and rc:
            mg.set_ospf_cost(rc, 100); actions.append(f"{rc}@100")
        metrics = mg.get_all_metrics()              # 검증 조회 (순수 조회 — 시간 진행 없음)
        if step > 1 and all(m["latency"] < 50 and m["packetLoss"] < 0.01 for m in metrics):
            return step, actions
    return 15, actions

ttrs, wasted = [], 0
for s in range(50):
    link = random.Random(s).choice(mg.LINKS_LIST)
    ttr, acts = episode(link, 42_000 + s); ttrs.append(ttr)
    wasted += sum(1 for a in acts if not a.startswith(link))
    if s < 3: print(f"  link={link} TTR={ttr} actions={acts}")
print(f"analytics-only, 1 tick/cycle: avg TTR={sum(ttrs)/len(ttrs):.2f}  "
      f"success={sum(t<15 for t in ttrs)/len(ttrs)*100:.0f}%  wasted actions(50ep)={wasted}")
