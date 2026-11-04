# EXPERIMENT KNOWLEDGE BASE

## OVERVIEW

Training, offline policy evaluation, HTTP closed-loop experiments, result provenance, and figure generation.

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Full reproduction | `reproduce.sh` | Training -> policy probe -> offline evaluation -> servers -> three HTTP experiments -> figures -> dashboard |
| Train / evaluate agents | `run_experiment.py` | Supplies the train/test link split; offline `NetworkEnv(local_mode=True)` |
| Component comparison | `ablation_study.py` | Analytics-only, MAML-only, combined; resets AI buffer each episode |
| Mixed-link recovery | `stress_test.py` | Held-out links receive double selection weight; resets AI buffer each episode |
| Adaptation across episodes | `persistent_buffer_test.py` | Clears AI buffer once, then retains it across simulator resets |
| Result metadata | `_resultmeta.py` | `result_meta()` merges provenance into each result document's top level |
| Figure generation | `make_figures.py`, `figures/figspec.py`, `figures/palette.py` | Fixed inputs and SVG/PNG outputs under `figures/out/` |
| Dashboard inputs | `make_dashboard_data.py` | Reads canonical result names; `--episodes` selects the stress file |
| External detector validation | `validate_security_detector.py`, `cicddos_loader.py` | Dataset instructions in `CICDDOS2019_SETUP.md` |

## EXPERIMENT CONTRACTS

- Train through `run_experiment.py`: it passes `TRAIN_LINKS` to both trainers. Train links are `r1-r2`, `r1-r3`, `r2-r3`, `r2-r4`; held-out test links are `r3-r4`, `r1-r4`. Direct agent training entry points do not enforce this split.
- Offline evaluation must specify `--summary-out`: its default `summary.json` is also consumed by `/live-results` for historical OODA results. Evaluation always overwrites `baseline_results.csv` and `fewshot_results.csv`, regardless of the summary filename.
- Offline evaluation runs up to 200 steps without Analytics overrides; reported success is `TTR < 30`. The HTTP experiments cap episodes at 15 cycles and report success as `TTR < 15`.
- Current HTTP cycles advance one simulator tick; verification reads do not advance time. `*_pre_audit.json` closed-loop results used two ticks per cycle. Keep `*_pre_b1.json` and `*_pre_simseed.json` in their historical measurement context too.
- Record `schema_version`, `timestamp`, `git_commit` (including `-dirty`), `seed`, `contract`, and `_condition` through `result_meta()`. A different obs/action/reward/sim contract prevents direct comparison; a filename alone does not establish compatibility.
- Global Python/NumPy/Torch seeds do not seed the simulator's private RNG. Offline `NetworkEnv(sim_seed=...)` seeds its backend once and continues that stream across resets.
- HTTP stress/persistent runs explicitly seed simulator reset with `seed * 1000 + ep` (episodes start at 1). Ablation uses `42000 + i` for identical noise across modes and always uses seed 42, independent of reproduction's `SEED` environment variable.

## REPRODUCTION SIDE EFFECTS

- `--quick` reduces training and episode counts; `--no-train` skips only checkpoint training. Policy probing, evaluations, server restart/cleanup, result writes, and figure generation still run.
- Use `--suffix=_name` syntax. Only policy-check, offline-summary, ablation, and stress JSON filenames receive the suffix. `persistent_buffer.json`, evaluation CSVs, and figure outputs remain shared and are overwritten; training checkpoints and curves are also overwritten unless `--no-train` is given.
- A nonempty suffix skips dashboard generation and its smoke check. It does not redirect figure inputs: `make_figures.py` still reads unsuffixed `ablation_study.json` and `policy_check.json`, plus training curves/checkpoint metadata.
- `stop_servers()` kills every process matched by `pgrep -f '^python3 api_server.py'` or `pgrep -f '^python3 mock_snmp_agent.py'`. It runs before server launch and via an `EXIT` trap, so reproduction can stop servers started outside this run.
- The pipeline launches simulator `:5001` and AI `:8000`; logs are overwritten at `/tmp/anm_snmp.log` and `/tmp/anm_ai.log`. Its process matching is hardcoded to `python3` even when `PYTHON` selects another executable.

## COMMANDS

Working directory for every command below: `vagus/autonomous-network-mgmt` (the application root). Reproduction requires Bash and Unix process tools. All four commands write artifacts; reproduction also restarts matching services.

```bash
bash experiments/reproduce.sh --quick --no-train --suffix=_comparison
python3 experiments/run_experiment.py --evaluate --episodes 10 --eval-links test --seed 42 --summary-out offline_eval_comparison.json
python3 experiments/make_figures.py
python3 experiments/make_dashboard_data.py --episodes=50
```

## FIGURE CONVENTIONS

- `figspec.save()` requires a source result document or explicit `condition=` and stamps provenance onto both SVG and PNG outputs. Pass the measured document through rather than reconstructing its metadata.
- Figure labels and condition stamps use ASCII; put Korean explanation in Markdown captions. Use `palette.py` and `figspec.py` for shared color, layout, and output conventions.
