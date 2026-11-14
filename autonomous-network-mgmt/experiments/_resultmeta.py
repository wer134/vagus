"""결과 파일 메타데이터 규약 (cowork/ROADMAP.md E-4).

모든 실험 결과 JSON은 `result_meta(...)`가 만든 블록을 최상위에 병합한다.
목적: **결과 파일만 보고 어떤 조건에서 측정됐는지 알 수 있게** 하는 것.
2026-09-09 감사에서 드러났듯이(AUDIT P1), 측정 방법이 바뀌면 이전 수치는 무효가 되는데
결과 파일에 그 조건이 없으면 나중에 구분할 수 없다.

기록 항목
  schema_version : 이 메타데이터 규약 자체의 버전
  git_commit     : 측정 시점의 커밋 (dirty 여부 포함) — 코드와 수치를 잇는 유일한 끈
  seed           : 재현용 seed (None이면 비고정)
  contract       : obs/action/reward/sim 버전. 하나라도 다르면 직접 비교 불가
  _condition     : 사람이 읽는 측정 조건 한 줄

사용:
    from _resultmeta import result_meta
    doc = {**result_meta(seed=42, condition="폐쇄 루프 /auto-step, 사이클당 1틱"), "results": ...}

자가 테스트: python experiments/_resultmeta.py
"""
import os
import subprocess
import sys
from datetime import datetime

SCHEMA_VERSION = 1

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=_PROJECT_ROOT,
            capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip()
    except Exception:
        return None


def git_commit() -> str | None:
    """현재 커밋 SHA. 워킹트리가 더러우면 `<sha>-dirty`.

    `experiments/results/` 아래의 변경은 세지 않는다. 실험 스크립트는 결과 파일을 쓰려고 연 뒤에
    이 함수를 부르므로, 결과 파일까지 세면 **코드가 깨끗해도 항상 dirty**가 된다 — 2026-11-14
    이전의 결과 파일이 전부 `-dirty`인 이유다. dirty 표시는 "측정한 코드가 커밋과 다른가"를
    말해야 하고, 결과 파일은 코드가 아니라 산출물이다.
    """
    sha = _run_git("rev-parse", "--short", "HEAD")
    if sha is None:
        return None
    prefix = _run_git("rev-parse", "--show-prefix") or ""
    status = _run_git(
        "status", "--porcelain", "--",
        ":(top)", f":(top,exclude){prefix}experiments/results",
    )
    return f"{sha}-dirty" if status else sha


def _load_by_path(alias: str, *relpath: str):
    """파일 경로로 모듈을 로드한다 (sys.path 순서에 의존하지 않음).

    `ai-engine/topology.py`(AI 엔진 상수)와 `simulation/topology.py`(Mininet 참조 구현)는
    모듈명이 같아 `import topology`의 결과가 sys.path 순서에 좌우된다. 여기서는 어느
    파일을 읽는지 못 박는다.
    """
    import importlib.util
    path = os.path.join(_PROJECT_ROOT, *relpath)
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def contract_versions() -> dict:
    """관측/행동/보상/시뮬레이터 계약 버전. 로드 실패 시 해당 항목만 None."""
    versions: dict[str, int | None] = {
        "obs_version": None, "action_version": None,
        "reward_version": None, "sim_version": None,
    }
    try:
        t = _load_by_path("_anm_topology", "ai-engine", "topology.py")
        versions["obs_version"]    = t.OBS_VERSION
        versions["action_version"] = t.ACTION_VERSION
    except Exception:
        pass
    try:
        versions["reward_version"] = _load_by_path(
            "_anm_reward", "ai-engine", "reward.py").REWARD_VERSION
    except Exception:
        pass
    try:
        versions["sim_version"] = _load_by_path(
            "_anm_metric_generator", "simulation", "metric_generator.py").SIM_VERSION
    except Exception:
        pass
    return versions


def result_meta(
    seed: int | None = None,
    condition: str | None = None,
    **extra,
) -> dict:
    """결과 문서 최상위에 병합할 메타데이터 블록."""
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp":      datetime.now().isoformat(),
        "git_commit":     git_commit(),
        "seed":           seed,
        "contract":       contract_versions(),
        "_condition":     condition,
        **extra,
    }


if __name__ == "__main__":
    m = result_meta(seed=42, condition="자가 테스트", n_per_mode=3)
    assert m["schema_version"] == SCHEMA_VERSION
    assert m["seed"] == 42 and m["n_per_mode"] == 3
    assert set(m["contract"]) == {"obs_version", "action_version", "reward_version", "sim_version"}
    # 이 리포에서는 네 버전이 모두 읽혀야 한다 (import 실패는 계약 추적 실패다)
    missing = [k for k, v in m["contract"].items() if v is None]
    assert not missing, f"계약 버전을 읽지 못함: {missing}"
    assert m["git_commit"], "git_commit을 읽지 못함"

    # 결과 파일을 쓰는 중이어도 dirty 판정이 바뀌지 않아야 한다
    before = git_commit()
    probe = os.path.join(_PROJECT_ROOT, "experiments", "results", "_selftest_probe.json")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("{}")
            f.flush()
            assert git_commit() == before, "results/ 아래의 파일이 dirty 판정을 바꿨다"
    finally:
        os.remove(probe)
    # 반대로 코드 변경은 잡아야 한다 (추적되지 않는 새 파일도 코드 변경이다)
    stray = os.path.join(_PROJECT_ROOT, "experiments", "_selftest_stray.py")
    try:
        open(stray, "w").close()
        assert git_commit().endswith("-dirty"), "코드 쪽 변경을 dirty로 잡지 못했다"
    finally:
        os.remove(stray)
    print("OK — dirty 판정: results/ 산출물은 무시, 코드 변경은 감지")
    print(f"OK — result_meta: commit={m['git_commit']} contract={m['contract']}")
    print("\n모든 자가 테스트 통과")
