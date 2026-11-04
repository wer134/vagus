#!/usr/bin/env bash
# 고정 연구 프로토콜을 새 디렉터리에서 재현한다.
# 사용: PYTHON=python3 bash scripts/reproduce_research.sh [새 출력 디렉터리]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
export PYTHONUTF8=1
if (( $# > 1 )); then
  printf '사용법: %s [새 출력 디렉터리]\n' "$0" >&2
  exit 2
fi
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  printf '사용법: PYTHON=python3 bash %s [새 출력 디렉터리]\n' "$0"
  printf '생략하면 experiments/research_runs 아래에 고유 디렉터리를 만듭니다.\n'
  exit 0
fi
OUT="${1:-$ROOT/experiments/research_runs/research_$(date -u +%Y%m%dT%H%M%SZ)_$$}"
if [[ -e "$OUT" || -L "$OUT" ]]; then
  printf '출력 경로가 이미 존재합니다: %s\n' "$OUT" >&2
  exit 2
fi
mkdir -p "$(dirname "$OUT")"
mkdir "$OUT"
OUT="$(cd "$OUT" && pwd)"
cd "$ROOT/experiments"

printf '연구 자가 테스트를 실행합니다.\n'
for module in recovery_control.test_simulation recovery_research.test_calibration \
  recovery_research.test_controllers recovery_research.test_cycle2 \
  recovery_research.test_statistics recovery_research.test_runner; do
  "$PY" -m "$module"
done
"$PY" "$ROOT/scripts/test_research_reporting.py"
printf '정책·소스·시나리오 프로토콜을 동결합니다: %s\n' "$OUT"
"$PY" -m recovery_research.runner freeze --output-dir "$OUT"
printf '개발 seed 평가를 실행합니다.\n'
"$PY" -m recovery_research.runner run --manifest "$OUT/manifest.json" --split dev
printf '동결한 test seed 평가를 실행합니다.\n'
"$PY" -m recovery_research.runner run --manifest "$OUT/manifest.json" --split test
printf '측정값에서 한국어 보고서와 도판을 생성합니다.\n'
"$PY" "$ROOT/scripts/research_cycle_report.py" --input "$OUT/test.json" --output "$OUT/FINDINGS.md"
"$PY" "$ROOT/scripts/research_findings_plot.py" --input "$OUT/test.json" --output-dir "$OUT/artifacts"
printf '재현 완료: %s\n' "$OUT"
