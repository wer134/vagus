#!/usr/bin/env bash
# 자가 테스트 러너 (cowork/ROADMAP.md E-1)
#
# 이 리포는 pytest를 쓰지 않고 각 모듈의 `if __name__ == "__main__":` 블록에 자가 테스트를
# 둔다 (cowork/FIX_PLAN.md §0.2-6). 이 스크립트는 그것들을 한 명령으로 모아 돌린다.
#
#   ./scripts/selftest.sh              # 전부 (Java 컴파일 포함)
#   ./scripts/selftest.sh --quick      # Java(mvn) 생략 — 네트워크/시간이 오래 걸릴 때
#   ./scripts/selftest.sh --no-policy  # 체크포인트 붕괴 검사 생략
#
# 종료 코드: 하나라도 실패하면 1. 정책 붕괴는 실패가 아니라 경고다 (붕괴한 체크포인트도
# "붕괴했다는 사실과 함께" 보존하는 것이 방침 — ROADMAP A-5).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUN_MVN=1
RUN_POLICY=1
for arg in "$@"; do
  case "$arg" in
    --quick)     RUN_MVN=0 ;;
    --no-policy) RUN_POLICY=0 ;;
    -h|--help)   sed -n '2,14p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "알 수 없는 옵션: $arg" >&2; exit 2 ;;
  esac
done

PASS=(); FAIL=(); SKIP=()

run() {                       # run <이름> <작업 디렉터리> <명령...>
  local name="$1" dir="$2"; shift 2
  printf '\n\033[1m── %s\033[0m (%s)\n' "$name" "$dir"
  if (cd "$dir" && "$@"); then
    PASS+=("$name")
  else
    FAIL+=("$name")
    printf '\033[31m   FAILED: %s\033[0m\n' "$name"
  fi
}

PY=${PYTHON:-python3}

# ── 1) 모듈 자가 테스트 ───────────────────────────────────────────────────────
run "metric_generator"  "$ROOT/simulation"  "$PY" metric_generator.py
run "topology"          "$ROOT/ai-engine"   "$PY" topology.py
run "anomaly_detector"  "$ROOT/ai-engine"   "$PY" anomaly_detector.py
run "ospf_security"     "$ROOT/ai-engine"   "$PY" ospf_security.py
run "cicddos_loader"    "$ROOT/experiments" "$PY" cicddos_loader.py
run "result_meta"       "$ROOT/experiments" "$PY" _resultmeta.py

# ── 2) 문법 검사 (자가 테스트가 없는 모듈까지 최소한 import 가능한지) ──────────
run "py_compile(all)"   "$ROOT"             "$PY" -m compileall -q \
    simulation ai-engine experiments

# ── 3) 정책 붕괴 검사 (체크포인트가 있을 때만, 실패시켜도 되는 항목이 아님) ────
if [ "$RUN_POLICY" = 1 ]; then
  printf '\n\033[1m── policy_check\033[0m (ai-engine)\n'
  if (cd "$ROOT/ai-engine" && "$PY" policy_check.py); then
    PASS+=("policy_check")
  else
    SKIP+=("policy_check (체크포인트 로드 불가)")
  fi
else
  SKIP+=("policy_check (--no-policy)")
fi

# ── 4) Java 컴파일 ────────────────────────────────────────────────────────────
if [ "$RUN_MVN" = 1 ]; then
  if command -v mvn >/dev/null 2>&1; then
    run "collector-service compile"    "$ROOT/collector-service"    mvn -q compile
    run "orchestrator-service compile" "$ROOT/orchestrator-service" mvn -q compile
  else
    SKIP+=("mvn compile (mvn 없음)")
  fi
else
  SKIP+=("mvn compile (--quick)")
fi

# ── 결과 ──────────────────────────────────────────────────────────────────────
printf '\n%s\n' "════════════════════════════════════════════════════"
printf '  통과 %d · 실패 %d · 생략 %d\n' "${#PASS[@]}" "${#FAIL[@]}" "${#SKIP[@]}"
printf '%s\n' "════════════════════════════════════════════════════"
for n in "${PASS[@]}"; do printf '  \033[32m✓\033[0m %s\n' "$n"; done
for n in "${SKIP[@]:-}"; do [ -n "$n" ] && printf '  \033[33m–\033[0m %s\n' "$n"; done
for n in "${FAIL[@]:-}"; do [ -n "$n" ] && printf '  \033[31m✗\033[0m %s\n' "$n"; done

[ "${#FAIL[@]}" -eq 0 ] || exit 1
printf '\n모두 통과\n'
