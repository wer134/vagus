# SIMULATION KNOWLEDGE BASE

## OVERVIEW

`metric_generator.py` owns the shared in-process network state; `mock_snmp_agent.py` exposes that model through Flask.

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Time, stress, metric noise | `metric_generator.py` | `tick`, `_update_link_stress`, `_stress_to_metrics` |
| Demand and OSPF routing | `metric_generator.py` | `_PATHS`, `_compute_link_loads`, `_compute_link_traffic` |
| Episode/probe isolation | `metric_generator.py` | `reset_state`, `snapshot`, `restore` |
| HTTP validation and clock | `mock_snmp_agent.py` | `update_ospf_cost`, `debug_tick`, `_start_realtime_clock` |
| Mininet reference | `topology.py` | `build_topology`, `set_link_param`; AI constants live in `../ai-engine/topology.py` |

## TIME AND REPRODUCIBILITY

- `tick(n)` advances link dynamics and the tick counter. Metric reads do not advance either; congestion injection, reset, and other explicit mutations still change state.
- Metric reads sample fresh noise from the same private `_rng` used by dynamics. Extra reads can therefore change later seeded trajectories; do not describe them as RNG-pure or promise identical repeated metrics.
- `SIM_CLOCK=lockstep` is the HTTP default. `POST /debug/tick` advances it; `/auto-step` calls once at cycle start, while local `NetworkEnv.step()` ticks after acting.
- `SIM_CLOCK=realtime:<ms>` starts a background clock when the Flask script runs directly. Keep clock ownership explicit when combining clients, or explicit ticks add to realtime ticks.
- Preserve the private `random.Random()` instance instead of drawing from module-global random state.
- `reset_state(seed=...)` clears events, resets costs/stress/ticks, and restores uniform demand. It preserves routing mode; omitting the seed leaves the RNG stream unreseeded.
- The model is a module singleton. Wrap temporary policy probes in `snapshot()` / `restore()` so training state, demand, mode, and RNG position are restored.
- Preserve the existing `RLock` around tick, metric sampling, reset, snapshots, and protected mutations used by threaded clients.

## ROUTING CONTRACT

- Default `shortest_path` assigns pair demand to minimum total OSPF-cost paths and splits equal-cost paths evenly (ECMP). Raising a link cost can increase load on alternative links.
- `cost_inverse` is the legacy reproduction mode: normalized inverse costs drive stress. Do not substitute it for the demand-routing model when comparing current results.
- `get_link_loads()` always computes shortest-path absolute loads; `get_routing_state()` also exposes mode-dependent `link_traffic`. Keep those diagnostics distinct in legacy mode.
- `SIM_VERSION = 3` identifies the demand/ECMP model. Review result-contract versioning when changing simulator behavior.
- `BYPASS_COST_THRESHOLD = 100` suppresses the injected congestion term at or above the threshold; actual traffic still follows routing costs.
- Keep that threshold synchronized with `../ai-engine/topology.py:BYPASS_COST`, and keep `NODES`, `LINKS_LIST`, and adjacency consistent with the AI constants.
- `set_demand()` updates supplied pairs; reset restores all pairs to `1.0`. Reapply nonuniform demand after each reset.

## HTTP BOUNDARY

- `PUT /ospf/costs/<link>` accepts integer costs only from `[10, 20, 50, 100, 200]`; invalid costs return 400 and unknown links return 404.
- The in-process `set_ospf_cost()` checks link identity but does not enforce the HTTP cost set. Preserve validation at the adapter boundary when changing either path.
- Link mutations canonicalize endpoint order through `_link_key`; retain canonical keys in exported state.
- `/debug/state` exposes tick, stress, congestion, costs, attack, and routing diagnostics without advancing simulation time.

## LOCAL CHECKS

Run from this directory after simulator changes:

```bash
python3 metric_generator.py
```

The direct module self-test covers read/tick separation, seeded stress, ECMP redistribution, legacy mode, recovery, detour stress, and snapshot/RNG restoration. For shared topology or bypass changes, also run `python3 topology.py` from `../ai-engine/`; this directory's namesake launches Mininet instead.

## ANTI-PATTERNS

- Do not move stress updates into GET handlers or metric getters; measurement frequency must not become simulation time.
- Do not reset the singleton from a diagnostic probe without restoring its snapshot.
- Do not use a wall-clock metric `timestamp` as the simulation step; use `tick`.
