"""
Ablation Study: Analytics vs Intelligence vs Combined
------------------------------------------------------
각 구성 요소의 기여도를 독립적으로 평가.

ZSM 관점:
  A: Analytics-only — Orient 결과를 직접 Act에 사용 (Intelligence 없음)
  B: Intelligence-only — MAML meta-init, Analytics override 없음
  C: Combined — ZSM Analytics + ENI Intelligence (현재 시스템)

시뮬레이션 시간 (2026-09-09): 사이클당 1틱 (검증 조회는 순수 조회, sleep 제거).
이전 결과(ablation_study_pre_audit.json)는 사이클당 2틱으로 측정됐다 — 직접 비교 불가.
추가 지표: wasted_actions(정상 링크 cost 변경 횟수), rca_all_cycles_ok.
"""
import json, random, time, urllib.request
from datetime import datetime
from pathlib import Path

AI   = "http://127.0.0.1:8000"
SNMP = "http://127.0.0.1:5001"
ALL_LINKS = ["r1-r2", "r1-r3", "r2-r3", "r2-r4", "r3-r4", "r1-r4"]


def _http(method, url, data=None, timeout=8):
    body = json.dumps(data).encode() if data is not None else b""
    req  = urllib.request.Request(
        url, data=body or None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

post   = lambda url, data=None: _http("POST",   url, data)
delete = lambda url:            _http("DELETE", url)
get    = lambda url:            _http("GET",    url)
put    = lambda url, data=None: _http("PUT",    url, data)


def _run_episode(link: str, disable_analytics: bool, disable_maml: bool) -> dict:
    params = f"?disable_analytics={str(disable_analytics).lower()}&disable_maml={str(disable_maml).lower()}"
    wasted, rca_all_ok, actions = 0, True, []
    for step in range(1, 16):
        d = post(f"{AI}/auto-step{params}")
        act, orient = d["act"], d["orient"]
        if act.get("applied"):
            actions.append(f'{act["link"]}@{act["cost"]}')
            if act["link"] != link and act.get("changed", True):
                wasted += 1   # 정상 링크의 cost가 실제로 바뀐 경우만
        if orient.get("anomaly_detected") and orient.get("root_cause_link") not in (link, None):
            rca_all_ok = False
        metrics = get(f"{SNMP}/metrics")
        all_ok  = all(m["latency"] < 50 and m["packetLoss"] < 0.01 for m in metrics)
        if all_ok and step > 1:
            return {"ttr": step, "resolved": True, "wasted": wasted,
                    "rca_all_ok": rca_all_ok, "actions": actions}
    return {"ttr": 15, "resolved": False, "wasted": wasted,
            "rca_all_ok": rca_all_ok, "actions": actions}


def run_episode_combined(link: str) -> dict:
    """Analytics ON + MAML ON (현재 시스템)."""
    return _run_episode(link, disable_analytics=False, disable_maml=False)


def run_episode_analytics_only(link: str) -> dict:
    """Analytics ON + MAML OFF."""
    return _run_episode(link, disable_analytics=False, disable_maml=True)


def run_episode_maml_only(link: str) -> dict:
    """Analytics OFF + MAML ON."""
    return _run_episode(link, disable_analytics=True, disable_maml=False)


def run_ablation(n_per_mode: int = 50, output: str | None = None):
    # 공통 링크 시퀀스 (재현성)
    random.seed(42)
    links = [random.choice(ALL_LINKS) for _ in range(n_per_mode)]

    modes = {
        "analytics_only": run_episode_analytics_only,
        "maml_only":      run_episode_maml_only,
        "combined":       run_episode_combined,
    }

    results = {}
    for mode_name, episode_fn in modes.items():
        print(f"\n{'='*40}", flush=True)
        print(f"  Mode: {mode_name}", flush=True)
        print(f"{'='*40}", flush=True)
        ttrs, wasteds, rca_oks, episodes = [], [], [], []

        for i, link in enumerate(links, 1):
            # 모드 간 동일 노이즈 시퀀스 (에피소드별 seed)
            post(f"{SNMP}/debug/reset", {"seed": 42_000 + i})
            post(f"{AI}/reset-buffer")
            post(f"{SNMP}/debug/congestion/{link}")

            ts = datetime.now().strftime('%H:%M:%S')
            res = episode_fn(link)
            ttrs.append(res["ttr"]); wasteds.append(res["wasted"]); rca_oks.append(res["rca_all_ok"])
            episodes.append({"link": link, **res})
            print(f"  [{ts}] Ep {i}/{n_per_mode} link={link} TTR={res['ttr']} resolved={res['resolved']} "
                  f"wasted={res['wasted']} act={res['actions']}", flush=True)

            try:
                delete(f"{SNMP}/debug/congestion/{link}")
            except Exception: pass

        avg_ttr  = sum(ttrs) / len(ttrs)
        success  = sum(1 for t in ttrs if t < 15) / len(ttrs) * 100
        results[mode_name] = {
            "avg_ttr": round(avg_ttr, 2), "success_rate": success,
            "wasted_actions_per_ep": round(sum(wasteds) / len(wasteds), 2),
            "rca_all_cycles_ok_pct": round(sum(rca_oks) / len(rca_oks) * 100, 1),
            "ttrs": ttrs, "episodes": episodes,
        }
        print(f"  -> avg TTR: {avg_ttr:.2f}  success: {success:.0f}%  "
              f"wasted/ep: {sum(wasteds)/len(wasteds):.2f}", flush=True)

    print(f"\n{'='*50}", flush=True)
    print(f"  ABLATION STUDY RESULTS ({n_per_mode} eps each)", flush=True)
    print(f"{'='*50}", flush=True)
    for mode, r in results.items():
        print(f"  {mode:<20}: TTR={r['avg_ttr']:.2f}  success={r['success_rate']:.0f}%  "
              f"wasted/ep={r['wasted_actions_per_ep']:.2f}  rca_all={r['rca_all_cycles_ok_pct']:.0f}%", flush=True)
    print(f"{'='*50}", flush=True)

    out = Path(__file__).parent / "results" / (output or "ablation_study.json")
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(), "n_per_mode": n_per_mode,
            "_condition": (
                "폐쇄 루프 /auto-step, 사이클당 시뮬레이터 1틱(lockstep), 검증 조회는 순수 조회, "
                "에피소드별 노이즈 seed 고정(42000+i). 2026-09-09 이전 결과는 사이클당 2틱."
            ),
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"Saved -> {out}", flush=True)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--episodes", type=int, default=50)
    p.add_argument("--output",   type=str, default=None, help="results/ 아래 파일명")
    a = p.parse_args()
    run_ablation(n_per_mode=a.episodes, output=a.output)
