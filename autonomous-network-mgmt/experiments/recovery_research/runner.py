"""동결과 평가를 분리하는 연구 CLI."""

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import subprocess
from typing import Literal, assert_never

from pydantic import ValidationError

from recovery_research.aggregation import build_payload
from recovery_research.artifacts import ExperimentPayload, FrozenModel, RunMetadata, Split, write_json
from recovery_research.evaluation import run_episode
from recovery_research.manifest import EvaluationError, Manifest, build_manifest, load_manifest, verify_manifest


class Arguments(FrozenModel):
    action: Literal["freeze", "run"]
    output_dir: Path | None = None
    manifest: Path | None = None
    split: Split = Split.DEV
    replace: bool = False


def freeze(output_dir: Path) -> Path:
    """빈 디렉터리에 새 실험만 만들고 기존 동결 자료를 덮어쓰지 않는다."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise EvaluationError("freeze requires an empty output directory")
    manifest = build_manifest()
    path = output_dir / "manifest.json"
    write_json(path, manifest)
    write_json(output_dir / "freeze.metadata.json", RunMetadata(created_at=datetime.now(UTC).isoformat(), manifest_digest=manifest.digest, payload_digest=manifest.digest, split=None))
    return path


def evaluate_bank(manifest: Manifest, split: Split) -> ExperimentPayload:
    episodes = tuple(run_episode(case, policy) for case in manifest.bank(split).cases for policy in manifest.policies)
    return build_payload(manifest, split, episodes)


def run_frozen(manifest_path: Path, split: Split, replace: bool = False) -> Path:
    """동결을 검증한 뒤 전체 비교를 수행한다; test 산출물은 불변이다."""
    manifest = load_manifest(manifest_path)
    frozen = RunMetadata.model_validate_json((manifest_path.parent / "freeze.metadata.json").read_text(encoding="utf-8"))
    if frozen.split is not None or frozen.manifest_digest != manifest.digest or frozen.payload_digest != manifest.digest:
        raise EvaluationError("manifest changed after freeze")
    output = manifest_path.parent / f"{split.value}.json"
    metadata = manifest_path.parent / f"{split.value}.metadata.json"
    if split == Split.TEST and replace:
        raise EvaluationError("test artifacts cannot be replaced")
    if not replace and (output.exists() or metadata.exists()):
        raise EvaluationError(f"existing {split.value} output is preserved")
    verify_manifest(manifest)
    payload = evaluate_bank(manifest, split)
    digest = sha256(payload.model_dump_json().encode()).hexdigest()
    write_json(output, payload, replace)
    write_json(metadata, RunMetadata(created_at=datetime.now(UTC).isoformat(), manifest_digest=manifest.digest, payload_digest=digest, split=split), replace)
    return output


def parse_arguments(argv: Sequence[str] | None = None) -> Arguments:
    parser = argparse.ArgumentParser(description="복구 정책 동결 및 짝지은 평가")
    commands = parser.add_subparsers(dest="action", required=True)
    freeze_parser = commands.add_parser("freeze", help="정책·시나리오·소스를 먼저 동결")
    freeze_parser.add_argument("--output-dir", type=Path, required=True)
    run_parser = commands.add_parser("run", help="기존 매니페스트를 검증하고 전체 split 실행")
    run_parser.add_argument("--manifest", type=Path, required=True)
    run_parser.add_argument("--split", choices=tuple(split.value for split in Split), required=True)
    run_parser.add_argument("--replace", action="store_true", help="기존 dev 산출물만 교체")
    return Arguments.model_validate(vars(parser.parse_args(list(argv) if argv is not None else None)))


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_arguments(argv)
        match args.action:
            case "freeze":
                if args.output_dir is None:
                    raise EvaluationError("freeze requires --output-dir")
                output = freeze(args.output_dir)
                print(f"평가 설정 동결 완료: {output}")
            case "run":
                if args.manifest is None:
                    raise EvaluationError("run requires --manifest")
                output = run_frozen(args.manifest, args.split, args.replace)
                print(f"짝지은 {args.split.value} 평가 저장 완료: {output}")
            case unreachable:
                assert_never(unreachable)
    except (EvaluationError, ValidationError, OSError, subprocess.CalledProcessError) as error:
        print(f"연구 실행 거부: {error}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
