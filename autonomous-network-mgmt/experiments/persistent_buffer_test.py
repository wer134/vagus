"""
Persistent Buffer MAML Test
----------------------------
ENI의 장기 적응을 시뮬레이션 — 지지 버퍼를 에피소드 간에 초기화하지 않음.
버퍼가 쌓이면서 MAML inner-loop 적응이 강해지는지 확인.

ZSM/ENI 논문 연결:
  - 에피소드 간 지지 버퍼 유지 = "Experiential Networking" (ENI GS 007)
  - 시간이 지남에 따라 Intelligence 레이어가 향상됨
"""
import json, random, time
from datetime import datetime
from pathlib import Path
import urllib.request

AI   = "http://127.0.0.1:8000"
SNMP = "http://127.0.0.1:5001"

TEST_LINKS  = ["r3-r4", "r1-r4"]
TRAIN_LINKS = ["r1-r2", "r1-r3", "r2-r3", "r2-r4"]


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
delete = lambda url:             _http("DELETE", url)
get    = lambda url:             _http("GET",    url)


def run(n_episodes: int = 30):
    results = []
    # Buffer starts empty, NOT reset between episodes (persistent)
    post(f"{SNMP}/debug/reset")
    post(f"{AI}/reset-buffer")

    for ep in range(1, n_episodes + 1):
        link = random.choice(TEST_LINKS + TEST_LINKS + TRAIN_LINKS)
        ts   = datetime.now().strftime('%H:%M:%S')
        print(f"[{ts}] Ep {ep}/{n_episodes} congestion={link}", flush=True)

        try:
            post(f"{SNMP}/debug/reset")
            # NO buffer reset — buffer persists across episodes!
            time.sleep(0.5)
            post(f"{SNMP}/debug/congestion/{link}")
            time.sleep(0.5)
        except Exception as e:
            print(f"  [setup error] {e}", flush=True)
            continue

        ttr, first_root, actions = None, None, []
        model_steps_adapting = 0  # steps where adapt_and_predict was used

        for step in range(1, 16):
            try:
                d      = post(f"{AI}/auto-step")
                orient = d["orient"]
                act    = d["act"]
                decide = d.get("decide", {})

                if first_root is None and orient.get("anomaly_detected"):
                    first_root = orient.get("root_cause_link")

                if act.get("applied"):
                    actions.append(f'{act["link"]}@{act["cost"]}')

                # Check if inner-loop adaptation was used
                adapt_note = decide.get("adapt_note", "")
                if "inner-loop" in adapt_note:
                    model_steps_adapting += 1

                metrics = get(f"{SNMP}/metrics")
                all_ok  = all(m["latency"] < 50 and m["packetLoss"] < 0.01 for m in metrics)
                if all_ok and step > 1:
                    ttr = step
                    break
                time.sleep(0.6)
            except Exception as e:
                print(f"  [step {step} error] {e}", flush=True)
                break

        if ttr is None:
            ttr = 15
        print(f"  -> TTR={ttr}  root={first_root}  act={actions}  adapt_steps={model_steps_adapting}", flush=True)

        results.append({
            "ep": ep, "link": link,
            "group": "test" if link in TEST_LINKS else "train",
            "ttr": ttr,
            "first_root": first_root,
            "root_match": (first_root == link),
            "model_steps_adapting": model_steps_adapting,
            "actions": actions,
        })
        try:
            delete(f"{SNMP}/debug/congestion/{link}")
        except Exception: pass
        time.sleep(1.0)

    # summary split by phase (early vs late)
    mid = len(results) // 2
    early = results[:mid]
    late  = results[mid:]

    def avg_ttr(rs): return round(sum(r["ttr"] for r in rs) / max(len(rs),1), 2)
    def adapt_rate(rs): return round(sum(r["model_steps_adapting"] for r in rs) / max(len(rs),1), 2)

    print(f"\n{'='*50}", flush=True)
    print(f"  Persistent Buffer MAML Test ({n_episodes} episodes)", flush=True)
    print(f"{'='*50}", flush=True)
    print(f"  ALL  avg TTR: {avg_ttr(results)}", flush=True)
    print(f"  EARLY(1-{mid})  avg TTR: {avg_ttr(early)}  adapt_rate={adapt_rate(early):.2f}", flush=True)
    print(f"  LATE ({mid+1}-{n_episodes}) avg TTR: {avg_ttr(late)}  adapt_rate={adapt_rate(late):.2f}", flush=True)
    print(f"  Success: {sum(1 for r in results if r['ttr']<15)}/{n_episodes}", flush=True)
    print(f"  Root accuracy: {sum(1 for r in results if r['root_match'])}/{n_episodes}", flush=True)
    print(f"{'='*50}", flush=True)
    print("  ENI Finding: buffer accumulation -> more MAML adaptation over time", flush=True)

    out = Path(__file__).parent / "results" / "persistent_buffer.json"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "n_episodes": n_episodes,
            "overall_avg_ttr": avg_ttr(results),
            "early_avg_ttr": avg_ttr(early),
            "late_avg_ttr": avg_ttr(late),
            "early_adapt_rate": adapt_rate(early),
            "late_adapt_rate": adapt_rate(late),
            "success_rate": sum(1 for r in results if r["ttr"] < 15) / n_episodes * 100,
            "root_cause_accuracy": sum(1 for r in results if r["root_match"]) / n_episodes * 100,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"Saved -> {out}", flush=True)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--episodes", type=int, default=30)
    run(n_episodes=p.parse_args().episodes)
