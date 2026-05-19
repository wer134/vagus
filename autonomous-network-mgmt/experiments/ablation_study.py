"""
Ablation Study: Analytics vs Intelligence vs Combined
------------------------------------------------------
각 구성 요소의 기여도를 독립적으로 평가.

ZSM 관점:
  A: Analytics-only — Orient 결과를 직접 Act에 사용 (Intelligence 없음)
  B: Intelligence-only — MAML meta-init, Analytics override 없음
  C: Combined — ZSM Analytics + ENI Intelligence (현재 시스템)
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
    for step in range(1, 16):
        post(f"{AI}/auto-step{params}")
        metrics = get(f"{SNMP}/metrics")
        all_ok  = all(m["latency"] < 50 and m["packetLoss"] < 0.01 for m in metrics)
        if all_ok and step > 1:
            return {"ttr": step, "resolved": True}
        time.sleep(0.6)
    return {"ttr": 15, "resolved": False}


def run_episode_combined(link: str) -> dict:
    """Analytics ON + MAML ON (현재 시스템)."""
    return _run_episode(link, disable_analytics=False, disable_maml=False)


def run_episode_analytics_only(link: str) -> dict:
    """Analytics ON + MAML OFF."""
    return _run_episode(link, disable_analytics=False, disable_maml=True)


def run_episode_maml_only(link: str) -> dict:
    """Analytics OFF + MAML ON."""
    return _run_episode(link, disable_analytics=True, disable_maml=False)


def run_ablation(n_per_mode: int = 50):
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
        ttrs = []

        for i, link in enumerate(links, 1):
            post(f"{SNMP}/debug/reset")
            post(f"{AI}/reset-buffer")
            time.sleep(0.5)
            post(f"{SNMP}/debug/congestion/{link}")
            time.sleep(0.5)

            ts = datetime.now().strftime('%H:%M:%S')
            res = episode_fn(link)
            ttrs.append(res["ttr"])
            print(f"  [{ts}] Ep {i}/{n_per_mode} link={link} TTR={res['ttr']} resolved={res['resolved']}", flush=True)

            try:
                delete(f"{SNMP}/debug/congestion/{link}")
            except Exception: pass
            time.sleep(0.8)

        avg_ttr  = sum(ttrs) / len(ttrs)
        success  = sum(1 for t in ttrs if t < 15) / len(ttrs) * 100
        results[mode_name] = {"avg_ttr": round(avg_ttr, 2), "success_rate": success, "ttrs": ttrs}
        print(f"  -> avg TTR: {avg_ttr:.2f}  success: {success:.0f}%", flush=True)

    print(f"\n{'='*50}", flush=True)
    print(f"  ABLATION STUDY RESULTS ({n_per_mode} eps each)", flush=True)
    print(f"{'='*50}", flush=True)
    for mode, r in results.items():
        print(f"  {mode:<20}: TTR={r['avg_ttr']:.2f}  success={r['success_rate']:.0f}%", flush=True)
    print(f"{'='*50}", flush=True)

    out = Path(__file__).parent / "results" / "ablation_study.json"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now().isoformat(), "n_per_mode": n_per_mode, "results": results}, f, indent=2)
    print(f"Saved -> {out}", flush=True)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--episodes", type=int, default=50)
    run_ablation(n_per_mode=p.parse_args().episodes)
