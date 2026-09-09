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

시뮬레이션 시간 (2026-09-09): /auto-step이 사이클마다 시뮬레이터를 1틱 진행시키고,
이 스크립트의 검증용 GET /metrics는 순수 조회다. 이전에는 검증 조회도 1틱을 진행시켜
사이클당 2틱이 흘렀고 TTR이 약 절반으로 측정됐다 (cowork/AUDIT_2026-09-09.md P1).
time.sleep은 시뮬레이션에 영향이 없어 제거했다.

추가 지표 (AUDIT P2):
  wasted_actions     — 주입 링크가 아닌 링크의 cost가 실제로 바뀐 횟수 (부수 피해; 같은 값 재설정은 제외)
  rca_all_cycles_ok  — 모든 사이클에서 root_cause_link ∈ {주입 링크, None}

실행:
  python experiments/stress_test.py [--episodes 20] [--seed 42] [--output results/stress_latest.json]
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


def run(n_episodes: int = 20, output: str | None = None, seed: int | None = 42):
    results = []
    rng = random.Random(seed)

    for ep in range(1, n_episodes + 1):
        # TEST 링크 2배 가중치 (일반화 평가 비중 높임)
        link   = rng.choice(TEST_LINKS + TEST_LINKS + TRAIN_LINKS)
        ts     = datetime.now().strftime('%H:%M:%S')
        print(f"[{ts}] Ep {ep}/{n_episodes} congestion={link}", flush=True)

        # ── 에피소드 초기화 ─────────────────────────────────────
        try:
            post(f"{SNMP}/debug/reset", {"seed": None if seed is None else seed * 1000 + ep})
            post(f"{AI}/reset-buffer")
            post(f"{SNMP}/debug/congestion/{link}")
        except Exception as e:
            print(f"  [setup error] {e}", flush=True)
            continue

        ttr          = None
        first_root   = None   # 첫 번째 OODA 사이클의 근본원인 (표시용)
        last_root    = None
        actions      = []
        wasted       = 0      # 주입 링크가 아닌 링크의 cost 변경 (부수 피해)
        rca_all_ok   = True   # 모든 사이클에서 root cause가 {주입 링크, None}인가
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
                if orient.get("anomaly_detected") and last_root not in (link, None):
                    rca_all_ok = False

                if act.get("applied"):
                    actions.append(f'{act["link"]}@{act["cost"]}')
                    if act["link"] != link and act.get("changed", True):
                        wasted += 1   # 정상 링크의 cost가 실제로 바뀐 경우만 (같은 값 재설정은 no-op)

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
                        f"last_root={last_root}  act={actions}  wasted={wasted}",
                        flush=True,
                    )
                    break
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
            "root_match": (first_root == link),  # Analytics 정확도 (첫 사이클)
            "rca_all_cycles_ok": rca_all_ok,
            "wasted_actions": wasted,
            "actions":    actions,
        })

        try:
            delete(f"{SNMP}/debug/congestion/{link}")
        except Exception:
            pass

    # ── 결과 집계 ───────────────────────────────────────────────
    all_ttrs   = [r["ttr"] for r in results]
    test_res   = [r for r in results if r["group"] == "test"]
    train_res  = [r for r in results if r["group"] == "train"]
    test_ttrs  = [r["ttr"] for r in test_res]  if test_res  else [0]
    train_ttrs = [r["ttr"] for r in train_res] if train_res else [0]
    success    = sum(1 for t in all_ttrs if t < 15)
    root_acc   = sum(1 for r in results if r["root_match"]) / len(results) * 100
    rca_all    = sum(1 for r in results if r["rca_all_cycles_ok"]) / len(results) * 100
    wasted_avg = sum(r["wasted_actions"] for r in results) / len(results)

    print("\n" + "=" * 55, flush=True)
    print("  ZSM/ENI OODA Loop Stress Test Results", flush=True)
    print("=" * 55, flush=True)
    print(f"  Total episodes   : {len(results)}", flush=True)
    print(f"  Overall avg TTR  : {sum(all_ttrs)/len(all_ttrs):.2f} steps", flush=True)
    print(f"  TEST  links TTR  : {sum(test_ttrs)/len(test_ttrs):.2f} steps  (n={len(test_res)})", flush=True)
    print(f"  TRAIN links TTR  : {sum(train_ttrs)/len(train_ttrs):.2f} steps  (n={len(train_res)})", flush=True)
    print(f"  Success rate     : {success}/{len(results)} ({success/len(results)*100:.0f}%)", flush=True)
    print(f"  Root-cause acc   : {root_acc:.1f}%  (first_root == congested_link)", flush=True)
    print(f"  RCA all cycles   : {rca_all:.1f}%  (every cycle root ∈ {{link, None}})", flush=True)
    print(f"  Wasted actions   : {wasted_avg:.2f} / episode  (cost changes on healthy links)", flush=True)
    print("=" * 55, flush=True)
    print("  ZSM Paper Connection:", flush=True)
    print("  - Orient (Analytics): SLA rules + adjacency-score RCA (IsolationForest는 /anomaly 전용)", flush=True)
    print("  - Decide (Intelligence): MAML inner-loop adaptation", flush=True)
    print("  - Analytics->Intelligence override: root cause adjacent to all violated nodes", flush=True)
    print("=" * 55, flush=True)

    summary = {
        "timestamp":     datetime.now().isoformat(),
        "total":         len(results),
        "avg_ttr":       round(sum(all_ttrs) / len(all_ttrs), 2),
        "test_avg_ttr":  round(sum(test_ttrs) / len(test_ttrs), 2),
        "train_avg_ttr": round(sum(train_ttrs) / len(train_ttrs), 2),
        "success_rate":  round(success / len(results) * 100, 1),
        "root_cause_accuracy_pct": round(root_acc, 1),
        "rca_all_cycles_ok_pct":   round(rca_all, 1),
        "wasted_actions_per_ep":   round(wasted_avg, 2),
        "seed":          seed,
        "_condition": (
            "폐쇄 루프 /auto-step, 사이클당 시뮬레이터 1틱(lockstep), 검증 조회는 순수 조회. "
            "2026-09-09 이전 결과는 사이클당 2틱으로 측정되어 직접 비교 불가."
        ),
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
    parser.add_argument("--seed",     type=int, default=42, help="링크 선택·시뮬레이터 노이즈 seed (-1: 비고정)")
    args = parser.parse_args()
    run(n_episodes=args.episodes, output=args.output,
        seed=None if args.seed is not None and args.seed < 0 else args.seed)
