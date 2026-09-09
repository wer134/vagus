#!/usr/bin/env bash
# 재현 파이프라인 (cowork/ROADMAP.md E-3)
#
# 학습 → 오프라인 평가 → 폐쇄 루프 실험 3종을 한 번에 돌려 README·문서의 모든 수치를
# 재생성한다. 결과 파일은 스크립트 출력으로만 갱신한다 (수기 편집 금지 — FIX_PLAN §0.2-2).
#
#   ./experiments/reproduce.sh                # 전체 (약 40~60분)
#   ./experiments/reproduce.sh --quick        # seed 1개 · 에피소드 축소 (약 10분)
#   ./experiments/reproduce.sh --no-train     # 기존 체크포인트로 평가만
#   ./experiments/reproduce.sh --suffix _b1   # 결과 파일명에 접미사 (전/후 병기용)
#
# 주의: --no-train을 주지 않으면 agents/의 운영 체크포인트를 **덮어쓴다.**
# 각 체크포인트 옆의 <name>.meta.json에 학습 조건·커밋·붕괴 검사 결과가 남는다.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY=${PYTHON:-python3}
SEED=${SEED:-42}
SUFFIX=""
TRAIN=1
EPISODES=50
PERSIST_EPISODES=30
MAML_ITERS=500
PPO_STEPS=50000

for arg in "$@"; do
  case "$arg" in
    --quick)     EPISODES=10; PERSIST_EPISODES=10; MAML_ITERS=100; PPO_STEPS=10000 ;;
    --no-train)  TRAIN=0 ;;
    --suffix=*)  SUFFIX="${arg#*=}" ;;
    --suffix)    echo "--suffix=<값> 형식으로 주세요" >&2; exit 2 ;;
    -h|--help)   sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "알 수 없는 옵션: $arg" >&2; exit 2 ;;
  esac
done

RESULTS="$ROOT/experiments/results"
SNMP_LOG=/tmp/anm_snmp.log
AI_LOG=/tmp/anm_ai.log

step() { printf '\n\033[1m═══ %s\033[0m  (%s)\n' "$1" "$(date -u +%H:%M:%S)"; }

stop_servers() {
  for pat in "^python3 api_server.py" "^python3 mock_snmp_agent.py"; do
    for pid in $(pgrep -f "$pat" 2>/dev/null); do kill "$pid" 2>/dev/null; done
  done
  sleep 1
}
trap stop_servers EXIT

# ── 1) 학습 ───────────────────────────────────────────────────────────────────
if [ "$TRAIN" = 1 ]; then
  step "MAML 메타학습 ($MAML_ITERS iters, seed $SEED)"
  (cd "$ROOT/ai-engine" && "$PY" agents/few_shot_agent.py --train \
      --meta-iterations "$MAML_ITERS" --seed "$SEED") || exit 1

  step "PPO 학습 ($PPO_STEPS steps, seed $SEED)"
  (cd "$ROOT/ai-engine" && "$PY" agents/baseline_drl.py --train \
      --timesteps "$PPO_STEPS" --seed "$SEED") || exit 1
else
  step "학습 생략 (--no-train) — 기존 체크포인트 사용"
fi

step "정책 붕괴 검사"
(cd "$ROOT/ai-engine" && "$PY" policy_check.py --json "$RESULTS/policy_check${SUFFIX}.json")

# ── 2) 오프라인 평가 (HTTP 없음) ──────────────────────────────────────────────
step "오프라인 평가 ($EPISODES ep, TEST 링크)"
(cd "$ROOT/experiments" && "$PY" run_experiment.py --evaluate \
    --episodes "$EPISODES" --eval-links test --seed "$SEED" \
    --summary-out "offline_eval${SUFFIX}.json") || exit 1

# ── 3) 서버 기동 ──────────────────────────────────────────────────────────────
step "mock_snmp_agent(:5001) + api_server(:8000) 기동"
stop_servers
(cd "$ROOT/simulation" && nohup "$PY" mock_snmp_agent.py > "$SNMP_LOG" 2>&1 &)
(cd "$ROOT/ai-engine"  && nohup "$PY" api_server.py      > "$AI_LOG"   2>&1 &)
for i in $(seq 1 30); do
  curl -sf localhost:5001/health >/dev/null 2>&1 && curl -sf localhost:8000/health >/dev/null 2>&1 && break
  sleep 1
done
curl -sf localhost:8000/health || { echo "AI 엔진 기동 실패 — $AI_LOG 확인" >&2; exit 1; }
echo

# ── 4) 폐쇄 루프 실험 ─────────────────────────────────────────────────────────
step "절제 실험 ($EPISODES ep × 3 모드)"
(cd "$ROOT/experiments" && "$PY" ablation_study.py \
    --episodes "$EPISODES" --output "ablation_study${SUFFIX}.json") || exit 1

step "스트레스 테스트 ($EPISODES ep)"
(cd "$ROOT/experiments" && "$PY" stress_test.py \
    --episodes "$EPISODES" --seed "$SEED" \
    --output "results/stress_${EPISODES}ep${SUFFIX}.json") || exit 1

step "지속 버퍼 ($PERSIST_EPISODES ep)"
(cd "$ROOT/experiments" && "$PY" persistent_buffer_test.py \
    --episodes "$PERSIST_EPISODES" --seed "$SEED") || exit 1

# ── 5) 요약 ───────────────────────────────────────────────────────────────────
step "요약"
"$PY" - "$RESULTS" "$SUFFIX" "$EPISODES" <<'PYEOF'
import json, os, sys
results, suffix, episodes = sys.argv[1], sys.argv[2], sys.argv[3]

def load(name):
    path = os.path.join(results, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

abl = load(f"ablation_study{suffix}.json")
st  = load(f"stress_{episodes}ep{suffix}.json")
off = load(f"offline_eval{suffix}.json")
pc  = load(f"policy_check{suffix}.json")

meta = abl or st or off or {}
print(f"  commit={meta.get('git_commit')}  contract={meta.get('contract')}")
print()
if st:
    print(f"  폐쇄 루프 (stress {st['total']} ep): TTR {st['avg_ttr']}  "
          f"성공 {st['success_rate']}%  RCA 첫/전 {st['root_cause_accuracy_pct']}%/"
          f"{st['rca_all_cycles_ok_pct']}%  부수피해 {st['wasted_actions_per_ep']}/ep")
if abl:
    for mode, r in abl["results"].items():
        print(f"  절제 {mode:16s}: TTR {r['avg_ttr']:6.2f}  성공 {r['success_rate']:5.1f}%  "
              f"부수피해 {r['wasted_actions_per_ep']}/ep")
if off:
    for agent in ("baseline", "fewshot"):
        if agent in off:
            a = off[agent]
            print(f"  오프라인 {agent:9s}: TTR {a['avg_ttr']:6.2f}  성공 {a['success_rate']:5.1f}%")
if pc:
    for name, r in pc.items():
        if r.get("loaded"):
            print(f"  정책검사 {name:9s}: {'COLLAPSED' if r['collapsed'] else 'ok':9s} "
                  f"top={r['top_action']} share={r['top_action_share']} "
                  f"entropy={r['action_entropy_bits']}bit")
PYEOF

step "완료"
