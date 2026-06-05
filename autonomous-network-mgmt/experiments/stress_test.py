"""
ZSM/ENI OODA Loop Stress Test
------------------------------
20~50 에피소드 동안 무작위 링크에 혼잡을 주입하고
OODA 자율 루프가 복구하는 데 걸리는 TTR(Time-To-Recovery)을 측정한다.

ZSM 3.1.1 매핑:
  Observe  : GET /metrics (SNMP 수집)
  Orient   : 이상 감지 + 근본 원인 분석 (IsolationForest + 인접도 점수)
  Decide   : MAML inner-loop 적응 → 행동 결정
  Act      : OSPF cost 변경
  Evaluate : ModelPerformanceTracker (ZSM 3.1.1.4)

ENI 연결:
  - few-shot 적응: 실시간 지지 버퍼(support buffer)로 inner-loop 적응
  - Analytics-Intelligence 계층: 고신뢰 근본원인 → Intelligence override

실행:
  python experiments/stress_test.py [--episodes 20] [--output results/stress_latest.json]
"""
import argparse
import json
import random
import time
from datetime import datetime
from pathlib import Path

import urllib.request
import urllib.error

AI   = "http://127.0.0.1:8000"
SNMP = "http://127.0.0.1:5001"

TEST_LINKS  = ["r3-r4", "r1-r4"]               # 학습에 사용하지 않은 링크 (일반화 평가)
TRAIN_LINKS = ["r1-r2", "r1-r3", "r2-r3", "r2-r4"]  # 학습 링크 (내삽 평가)


def _http(method: str, url: str, data=None, timeout: int = 8):
    body = json.dumps(data).encode() if data is not None else b""
    req  = urllib.request.Request(
        url, data=body or None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def post(url, data=None):   return _http("POST",   url, data)
def delete(url):            return _http("DELETE", url)
def get(url):               return _http("GET",    url)


def run(n_episodes: int = 20, output: str | None = None):
    results = []

    for ep in range(1, n_episodes + 1):
        # TEST 링크 2배 가중치 (일반화 평가 비중 높임)
        link   = random.choice(TEST_LINKS + TEST_LINKS + TRAIN_LINKS)
        ts     = datetime.now().strftime('%H:%M:%S')
        print(f"[{ts}] Ep {ep}/{n_episodes} congestion={link}", flush=True)

        # ── 에피소드 초기화 ─────────────────────────────────────
        try:
            post(f"{SNMP}/debug/reset")
            post(f"{AI}/reset-buffer")
            time.sleep(0.5)
            post(f"{SNMP}/debug/congestion/{link}")
            time.sleep(0.5)
        except Exception as e:
            print(f"  [setup error] {e}", flush=True)
            continue

        ttr          = None
        first_root   = None   # 첫 번째 OODA 사이클의 근본원인 (표시용)
        last_root    = None
        actions      = []
        reasoning    = []

        for step in range(1, 16):
            try:
                d         = post(f"{AI}/auto-step")
                orient    = d["orient"]
                act       = d["act"]
                last_root = orient.get("root_cause_link")

                # 첫 번째 이상 감지 사이클에서 근본원인 기록
                if first_root is None and orient.get("anomaly_detected"):
                    first_root = last_root

                if act.get("applied"):
                    actions.append(act["link"])

                # ZSM reasoning_chain 요약 (첫 번째만 저장)
                if step == 1 and "reasoning_chain" in d:
                    reasoning.append(d["reasoning_chain"])

                # 복구 확인: 직접 SNMP 조회
                metrics = get(f"{SNMP}/metrics")
                all_ok  = all(
                    m["latency"] < 50 and m["packetLoss"] < 0.01
                    for m in metrics
                )
                if all_ok and step > 1:
                    ttr = step
                    print(
                        f"  -> TTR={ttr}  first_root={first_root}  "
                        f"last_root={last_root}  act={actions}",
                        flush=True,
                    )
                    break
                time.sleep(0.6)
            except Exception as e:
                print(f"  [step {step} error] {e}", flush=True)
                break

        if ttr is None:
            ttr = 15
            print(
                f"  -> TIMEOUT  first_root={first_root}  last_root={last_root}  act={actions}",
                flush=True,
            )

        results.append({
            "ep":         ep,
            "link":       link,
            "group":      "test" if link in TEST_LINKS else "train",
            "ttr":        ttr,
            "first_root": first_root,
            "root_match": (first_root == link),  # Analytics 정확도
            "actions":    actions,
        })

        try:
            delete(f"{SNMP}/debug/congestion/{link}")
        except Exception:
            pass
        time.sleep(1.0)

    # ── 결과 집계 ───────────────────────────────────────────────
    all_ttrs   = [r["ttr"] for r in results]
    test_res   = [r for r in results if r["group"] == "test"]
    train_res  = [r for r in results if r["group"] == "train"]
    test_ttrs  = [r["ttr"] for r in test_res]  if test_res  else [0]
    train_ttrs = [r["ttr"] for r in train_res] if train_res else [0]
    success    = sum(1 for t in all_ttrs if t < 15)
    root_acc   = sum(1 for r in results if r["root_match"]) / len(results) * 100

    print("\n" + "=" * 55, flush=True)
    print("  ZSM/ENI OODA Loop Stress Test Results", flush=True)
    print("=" * 55, flush=True)
    print(f"  Total episodes   : {len(results)}", flush=True)
    print(f"  Overall avg TTR  : {sum(all_ttrs)/len(all_ttrs):.2f} steps", flush=True)
    print(f"  TEST  links TTR  : {sum(test_ttrs)/len(test_ttrs):.2f} steps  (n={len(test_res)})", flush=True)
    print(f"  TRAIN links TTR  : {sum(train_ttrs)/len(train_ttrs):.2f} steps  (n={len(train_res)})", flush=True)
    print(f"  Success rate     : {success}/{len(results)} ({success/len(results)*100:.0f}%)", flush=True)
    print(f"  Root-cause acc   : {root_acc:.1f}%  (first_root == congested_link)", flush=True)
    print("=" * 55, flush=True)
    print("  ZSM Paper Connection:", flush=True)
    print("  - Orient (Analytics): IsolationForest + adjacency scoring", flush=True)
    print("  - Decide (Intelligence): MAML inner-loop adaptation", flush=True)
    print("  - Analytics->Intelligence override: nodes_sharing_root>=2", flush=True)
    print("=" * 55, flush=True)

    summary = {
        "timestamp":     datetime.now().isoformat(),
        "total":         len(results),
        "avg_ttr":       round(sum(all_ttrs) / len(all_ttrs), 2),
        "test_avg_ttr":  round(sum(test_ttrs) / len(test_ttrs), 2),
        "train_avg_ttr": round(sum(train_ttrs) / len(train_ttrs), 2),
        "success_rate":  round(success / len(results) * 100, 1),
        "root_cause_accuracy_pct": round(root_acc, 1),
        "results":       results,
    }

    out_path = Path(output) if output else Path(__file__).parent / "results" / "stress_latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nSaved -> {out_path}", flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=20)
    parser.add_argument("--output",   type=str, default=None)
    args = parser.parse_args()
    run(n_episodes=args.episodes, output=args.output)
