"""Abilene TRAIN 시나리오 전체의 풀이 존재 표 (사후 진단 커리큘럼용, 2026-11-18).

시나리오 공간(혼잡 105 × 약한 링크 15 × 수요 변화 12 = 18,900) 중 TRAIN 분할 전부에 대해, 최종
상태(수요 변화 후)에 SLA 풀이가 있는지 탐색 오라클로 판정한다. 노이즈 seed와 무관한 성질이라
키 하나에 한 번이면 된다. TEST 키는 계산하지 않는다 — 학습이 TEST를 들여다보지 않게.

실행: python3 experiments/abilene_solvable_table.py
"""
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import scale_benchmark as sb  # noqa: E402  (경로 설정 포함)
import scale_env as se  # noqa: E402
from _resultmeta import result_meta  # noqa: E402
from netsim import NetSim, ABILENE  # noqa: E402

_SIM = {}


def _job(scn):
    if "sim" not in _SIM:
        _SIM["sim"] = NetSim(ABILENE)
    return scn.key, sb.final_state_oracle(_SIM["sim"], scn, 0)["solvable"]


if __name__ == "__main__":
    train = [s for s in se.all_scenarios("abilene") if s.split == "train"]
    t0 = time.time()
    with Pool(4) as p:
        table = dict(p.imap_unordered(_job, train, chunksize=32))
    n_ok = sum(table.values())
    print(f"TRAIN 시나리오 {len(table)}개 중 풀이 있음 {n_ok} ({n_ok / len(table):.1%}), {time.time() - t0:.0f}s")
    with open(HERE / "results" / "abilene_train_solvable.json", "w", encoding="utf-8") as f:
        json.dump({**result_meta(seed=0, condition="탐색 오라클(예산 30,000), 최종 상태 기준, TRAIN 키 전부"),
                   "n": len(table), "n_solvable": n_ok, "solvable": dict(sorted(table.items()))},
                  f, indent=1, ensure_ascii=False)
