# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# 실행: experiments 디렉터리에서 python -m recovery_control.test_simulation
"""관측·진실 분리와 물리적 보존을 검증하는 assertion self-test."""

from dataclasses import replace
from math import isclose

from .routing import WeightedPath, route_loads, route_plan
from .scenario import Scenario
from .simulation import Simulator
from .types import DEFAULT_COSTS, DETOUR_COMMAND, NODES, RESTORE_COMMAND, ContractError, CostCommand, Demands


def test_ecmp_and_detour() -> None:
    demands = Demands(180.0, 15.0)
    initial = route_loads(DEFAULT_COSTS, demands)
    assert initial.values == (90.0, 90.0, 90.0, 90.0, 15.0)
    detour = route_loads(DEFAULT_COSTS.with_value("a-b", 100.0), demands)
    assert detour.values == (0.0, 180.0, 0.0, 180.0, 15.0)
    routes = route_plan(DEFAULT_COSTS)
    assert len(routes.target) == 2
    for path in routes.target + routes.background:
        assert len(path.nodes) == len(set(path.nodes))


def test_ack_delay_and_conservation() -> None:
    sim = Simulator(Scenario(seed=7, incident_start=2, incident_end=7, propagation_delay=2))
    sim.advance()
    ack = sim.issue(DETOUR_COMMAND)
    assert ack.acknowledged_at == 1
    assert sim.truth_snapshot().costs == DEFAULT_COSTS
    sim.advance()
    assert sim.truth_snapshot().costs == DEFAULT_COSTS
    sim.advance()
    active = sim.truth_snapshot()
    assert active.costs["a-b"] == 100.0
    assert 0.0 < active.residual_fraction < 1.0
    assert not active.safe
    for _ in range(8):
        sim.advance()
        truth = sim.truth_snapshot()
        assert isclose(truth.loads["a-b"] + truth.loads["a-c"], truth.demands.target)
    assert sim.truth_snapshot().settled


def test_restore_requires_convergence() -> None:
    sim = Simulator(Scenario(seed=4, propagation_delay=0, convergence_ticks=4))
    sim.issue(DETOUR_COMMAND)
    assert sim.truth_snapshot().costs == DEFAULT_COSTS
    for _ in range(4):
        sim.advance()
    sim.issue(RESTORE_COMMAND)
    sim.advance()
    truth = sim.truth_snapshot()
    assert truth.costs == DEFAULT_COSTS
    assert not truth.restored
    for _ in range(3):
        sim.advance()
    assert sim.truth_snapshot().restored


def test_exogenous_pairing_and_reorder() -> None:
    scenario = Scenario(seed=19, observation_delay=2, delay_jitter=5, missing_probability=0.2, load_noise=0.03)
    control, intervention = Simulator(scenario), Simulator(scenario)
    intervention.issue(DETOUR_COMMAND)
    control_samples: list[int] = []
    for _ in range(scenario.horizon):
        left, right = control.advance(), intervention.advance()
        assert tuple((obs.sampled_at, obs.arrived_at) for obs in left) == tuple((obs.sampled_at, obs.arrived_at) for obs in right)
        assert control.truth_snapshot().demands == intervention.truth_snapshot().demands
        assert tuple(obs.reported_demands for obs in left) == tuple(obs.reported_demands for obs in right)
        control_samples.extend(obs.sampled_at for obs in left)
    assert len(control_samples) < scenario.horizon
    assert any(new < old for old, new in zip(control_samples, control_samples[1:], strict=False))
    assert Scenario(seed=19) == Scenario(seed=19)


def test_transit_and_missing_initial() -> None:
    scenario = Scenario(seed=3, background_via_source=True)
    sim = Simulator(scenario)
    observations = sim.advance()
    assert observations[0].background_via_source
    truth = sim.truth_snapshot()
    assert isclose(truth.loads["a-b"] + truth.loads["a-c"], truth.demands.target + 2 * truth.demands.background)
    missing = Simulator(replace(scenario, initial_missing=True))
    assert missing.advance() == ()
    assert missing.advance()[0].sampled_at == 2


def test_interrupted_transition_conserves_every_node() -> None:
    sim = Simulator(Scenario(seed=11, propagation_delay=1, incident_start=2, incident_end=5))
    sim.issue(DETOUR_COMMAND)
    sim.advance()
    sim.issue(RESTORE_COMMAND)
    for _ in range(8):
        sim.advance()
        truth = sim.truth_snapshot()
        for paths, demand, endpoints in ((truth.forwarding.target, truth.demands.target, ("a", "d")), (truth.forwarding.background, truth.demands.background, ("b", "c"))):
            balance = dict.fromkeys(NODES, 0.0)
            for path in paths:
                for left, right in zip(path.nodes, path.nodes[1:], strict=False):
                    balance[left] += demand * path.share
                    balance[right] -= demand * path.share
            assert isclose(balance[endpoints[0]], demand, abs_tol=1e-9)
            assert isclose(balance[endpoints[1]], -demand, abs_tol=1e-9)
            assert all(abs(balance[node]) < 1e-9 for node in NODES if node not in endpoints)
    assert sim.truth_snapshot().restored


def test_delayed_sample_preserves_sampled_state() -> None:
    sim = Simulator(Scenario(seed=9, observation_delay=3, propagation_delay=1, demand_bias=0.6))
    sim.advance()
    sim.advance()
    sampled_truth = sim.truth_snapshot()
    sim.issue(DETOUR_COMMAND)
    sim.advance()
    sim.advance()
    delayed = sim.advance()[0]
    assert delayed.sampled_at == 2 and delayed.arrived_at == 5
    assert delayed.costs == DEFAULT_COSTS
    assert delayed.loads == sampled_truth.loads
    assert isclose(delayed.reported_demands.target, sampled_truth.demands.target * 0.6)
    assert sim.truth_snapshot().costs != delayed.costs


def test_boundary_rejects_invalid_paths_and_commands() -> None:
    try:
        WeightedPath(("a", "b", "a", "c", "d"), 1.0)
    except ContractError as error:
        assert error.field == "path"
    else:
        raise AssertionError("cycle accepted")
    try:
        CostCommand("a-b", 0.0)
    except ContractError as error:
        assert error.field == "cost"
    else:
        raise AssertionError("zero cost accepted")
    try:
        Demands(-1.0, 2.0)
    except ContractError as error:
        assert error.field == "demands"
    else:
        raise AssertionError("negative demand accepted")


def main() -> None:
    tests = (test_ecmp_and_detour, test_ack_delay_and_conservation, test_restore_requires_convergence, test_exogenous_pairing_and_reorder, test_transit_and_missing_initial, test_interrupted_transition_conserves_every_node, test_delayed_sample_preserves_sampled_state, test_boundary_rejects_invalid_paths_and_commands)
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS simulation: {len(tests)} checks")


if __name__ == "__main__":
    main()
