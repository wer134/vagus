# AI ENGINE KNOWLEDGE BASE

## OVERVIEW

FastAPI OODA control, SLA diagnosis, MAML/PPO policies, and the shared Gymnasium observation/action contract.

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| API / closed loop | `api_server.py` | `_auto_step_locked`, observation helpers, actuation and support buffer |
| Topology / encoding | `topology.py` | Canonical AI node/link order, costs, normalization and versions |
| Simulation adapter | `environment/network_env.py` | Local module or HTTP backend; one tick after each action |
| SLA diagnosis / RCA | `anomaly_detector.py` | `diagnose`, `root_cause_analysis`; separate statistical detectors |
| Policy training / inference | `agents/few_shot_agent.py`, `agents/baseline_drl.py` | MAML functional adaptation and deterministic PPO inference |
| Reward | `reward.py` | `compute_reward` and `REWARD_VERSION` |
| Checkpoint diagnostics | `policy_check.py` | Action distribution probes, training curves, `.meta.json` sidecars |
| OSPF attack checks | `ospf_security.py` | Authentication, replay and sequence-jump checks |

## OBSERVATION AND ACTION CONTRACT

- Observations are 14 `float32` values: `[4 bandwidths / 1000, 4 latencies / 200, 6 costs / 200]`, clipped to `[0, 1]`.
- Node order is `r1,r2,r3,r4`; link order is `r1-r2,r1-r3,r2-r3,r2-r4,r3-r4,r1-r4`. Keep API helpers, `NetworkEnv`, simulator constants and Java `AiEngineClient` aligned.
- `Discrete(30)` encodes `link_idx * 5 + cost_idx`; costs are `[10,20,50,100,200]`. There is no policy NO-OP; action `0` means `r1-r2@10`.
- Observation/action semantics are versioned by `OBS_VERSION` / `ACTION_VERSION`; reward changes use `REWARD_VERSION`. Update the applicable contract version when changing semantics; `../experiments/_resultmeta.py` records these boundaries.
- `/action` accepts raw values and rejects lengths other than `4/4/6` with HTTP 400. It returns a decision; it does not apply OSPF changes.
- `/diagnose` and `/auto-step` return HTTP 503 when `_fetch_snmp` cannot obtain valid-sized metrics/cost data. Do not replace unavailable observations with healthy telemetry.
- `NetworkEnv` HTTP metric fetches raise on failure. Its observation conversion and API conversion are separate implementations, so check both when changing normalization.

## CLOSED-LOOP STATE

- `/auto-step` ticks before observation; `NetworkEnv.step` applies an action then ticks. Metric reads do not advance the simulator clock.
- Orient uses SLA rules (`latency > 50 ms` or `packetLoss > 0.01`) and adjacency RCA. `AnomalyDetector`'s IsolationForest decision belongs to `/anomaly`; security traffic detection is separate again.
- RCA can return `None` for residual stress after a covering link has been bypassed. Analytics-only mode then waits; do not reinterpret this as a missing link to replace arbitrarily.
- Analytics can override a MAML link choice with the root cause at cost `100` when at least two violated nodes support it. Preserve this distinction when interpreting decisions.
- The support buffer holds at most 32 `(previous_obs, previous_action, current_reward)` tuples. Append only if the previous action was actually applied; no-action and failed-actuation steps leave `_prev_action=None`.
- Adaptation starts at four support samples: `/auto-step` uses three inner steps, `/action` uses two. `FewShotAgent` adapts copied parameters rather than mutating its base model.
- `_loop_lock` serializes `/auto-step` and `/reset-buffer`; `/action` locks only while copying support samples. Other detector endpoints also access shared state, so do not assume the lock protects every API path.
- Failed actuation is reported as `act.applied=False` with `act.error`; successful application and an actual cost change are separate response fields.

## TRAINING AND CHECKPOINTS

- Run training through `../experiments/run_experiment.py`: it passes `TRAIN_LINKS` and keeps `r3-r4,r1-r4` held out. Direct agent `--train` entry points omit `train_links` and default to all links.
- `is_ready()` means a checkpoint loaded successfully. Inspect `load_error` and `/health` readiness fields; readiness does not establish learned policy quality.
- `/action` returns 503 for an unavailable selected model; `/auto-step` instead reports `MAML not trained` with no applied action when that policy is required.
- Post-training `write_checkpoint_meta` writes the checkpoint basename's `.meta.json` with training details and policy statistics. Probe failures can skip it; a model file alone does not establish that the check ran.
- `policy_check.py` marks collapse at `top_action_share >= 0.8`; inspect entropy and distinct actions alongside readiness. Training probes use simulator snapshot/restore to preserve its state and RNG stream.
- Current reward v1 uses latency, throughput and worst-node SLA penalties; it has no action-cost or collateral-damage term. Account for that limitation when changing control behavior.

## TARGETED VERIFICATION

- `topology.py` self-check compares node/link order, adjacency and bypass threshold with `../simulation/metric_generator.py`.
- `anomaly_detector.py` self-check covers initial RCA, already-handled residual stress, a new congestion pair, ambiguous multi-node cases and healthy state.
- `ospf_security.py` self-check covers replay, authentication downgrade, compromised-key sequence jumps and weak algorithms.
- For API changes, specifically verify normalized input equivalence, `4/4/6` rejection, observation failure, failed actuation and support attribution across no-action steps. `api_server.py`'s main block starts Uvicorn; it is not a self-test.
