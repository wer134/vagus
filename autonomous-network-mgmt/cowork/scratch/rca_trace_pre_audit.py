"""Analytics-only 폐쇄 루프를 HTTP 없이 재현 — RCA 2번째 스텝 이후 오판 + 관측 횟수=시뮬레이션 시간 확인."""
import sys, types, random
# sklearn/numpy 미설치 → 스텁 (diagnose()는 순수 파이썬)
np = types.ModuleType("numpy"); np.array = lambda *a, **k: None; sys.modules["numpy"] = np
sk = types.ModuleType("sklearn"); ske = types.ModuleType("sklearn.ensemble")
ske.IsolationForest = lambda *a, **k: None; sk.ensemble = ske
sys.modules["sklearn"] = sk; sys.modules["sklearn.ensemble"] = ske

sys.path.insert(0, "autonomous-network-mgmt/simulation"); sys.path.insert(0, "autonomous-network-mgmt/ai-engine")
import metric_generator as mg
from anomaly_detector import diagnose, _NODE_LINKS

def rca(diag, ospf):
    cands = diag.get("unhandled_links", []) or diag.get("suspected_links", [])
    if not cands: return None
    v = set(diag["violated_nodes"])
    return min(cands, key=lambda lk: (-sum(1 for n in v if lk in _NODE_LINKS.get(n, [])), ospf.get(lk, 10)))

def episode(link, ticks_per_step, seed):
    random.seed(seed); mg.reset_state(); mg.inject_congestion(link)
    actions = []
    for step in range(1, 16):
        metrics = mg.get_all_metrics()              # /auto-step 내부 관측 (tick 1)
        ospf = mg.get_ospf_costs(); d = diagnose(metrics, ospf); rc = rca(d, ospf)
        if d["anomaly_detected"] and rc:
            mg.set_ospf_cost(rc, 100); actions.append(f"{rc}@100")
        for _ in range(ticks_per_step - 1):
            metrics = mg.get_all_metrics()          # 측정 스크립트의 검증용 /metrics 호출 (tick 2)
        ok = all(m["latency"] < 50 and m["packetLoss"] < 0.01 for m in metrics)
        if ok and step > 1:
            return step, actions
    return 15, actions

for tps in (1, 2):
    ttrs = []; wrong = 0
    for s in range(50):
        link = random.Random(s).choice(mg.LINKS_LIST)
        ttr, acts = episode(link, tps, s); ttrs.append(ttr)
        wrong += sum(1 for a in acts if not a.startswith(link))
        if s < 3: print(f"  ticks/step={tps} link={link} TTR={ttr} actions={acts}")
    print(f"ticks/step={tps}: avg TTR={sum(ttrs)/len(ttrs):.2f}  healthy-link cost changes(50ep)={wrong}\n")
