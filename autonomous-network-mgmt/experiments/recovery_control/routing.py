"""단순 최단 경로별 균등 분배와 현재 수요의 흐름 보존."""

from dataclasses import dataclass
from math import isclose, isfinite

from .types import EDGES, NODES, ContractError, Demands, LinkValues


def edge_between(left: str, right: str) -> str:
    """방향에 독립적인 링크 이름을 반환한다."""
    edge = "-".join(sorted((left, right)))
    if edge not in EDGES:
        raise ContractError("path", f"nonadjacent nodes {left}, {right}")
    return edge


@dataclass(frozen=True, slots=True)
class WeightedPath:
    """현재 commodity의 단순 경로 하나와 전송 비율."""

    nodes: tuple[str, ...]
    share: float

    def __post_init__(self) -> None:
        if len(self.nodes) < 2 or len(self.nodes) != len(set(self.nodes)):
            raise ContractError("path", "requires at least two distinct nodes without cycles")
        if any(node not in NODES for node in self.nodes):
            raise ContractError("path", "unknown node")
        if not isfinite(self.share) or not 0 < self.share <= 1:
            raise ContractError("share", "requires finite fraction in (0,1]")
        for left, right in zip(self.nodes, self.nodes[1:], strict=False):
            _ = edge_between(left, right)


@dataclass(frozen=True, slots=True)
class Forwarding:
    """두 commodity의 비율 총합과 시작·종착 노드를 보장한다."""

    target: tuple[WeightedPath, ...]
    background: tuple[WeightedPath, ...]

    def __post_init__(self) -> None:
        for paths, source, destination in ((self.target, "a", "d"), (self.background, "b", "c")):
            if not isclose(sum(path.share for path in paths), 1.0, abs_tol=1e-12):
                raise ContractError("forwarding", "commodity shares must sum to one")
            if any(path.nodes[0] != source or path.nodes[-1] != destination for path in paths):
                raise ContractError("forwarding", "wrong commodity endpoints")


def _shortest_paths(costs: LinkValues, endpoints: tuple[str, str]) -> tuple[WeightedPath, ...]:
    candidates: list[tuple[float, tuple[str, ...]]] = []
    frontier: list[tuple[str, ...]] = [(endpoints[0],)]
    while frontier:
        nodes = frontier.pop()
        if nodes[-1] == endpoints[1]:
            cost = sum(costs[edge_between(left, right)] for left, right in zip(nodes, nodes[1:], strict=False))
            candidates.append((cost, nodes))
            continue
        for node in NODES:
            adjacent = "-".join(sorted((nodes[-1], node))) in EDGES
            if node not in nodes and adjacent:
                frontier.append((*nodes, node))
    minimum = min(cost for cost, _ in candidates)
    selected = sorted(nodes for cost, nodes in candidates if isclose(cost, minimum, rel_tol=0, abs_tol=1e-9))
    return tuple(WeightedPath(nodes, 1.0 / len(selected)) for nodes in selected)


def route_plan(costs: LinkValues, background_via_source: bool = False) -> Forwarding:
    """배경 source 경유는 알려진 b→a→c waypoint 정책이다."""
    if any(cost <= 0 for cost in costs.values):
        raise ContractError("costs", "requires positive routing costs")
    background = (WeightedPath(("b", "a", "c"), 1.0),) if background_via_source else _shortest_paths(costs, ("b", "c"))
    return Forwarding(_shortest_paths(costs, ("a", "d")), background)


def forwarding_loads(forwarding: Forwarding, demands: Demands) -> LinkValues:
    """과거 부하 대신 현재 수요를 각 경로 비율에 곱한다."""
    loads = dict.fromkeys(EDGES, 0.0)
    for paths, demand in ((forwarding.target, demands.target), (forwarding.background, demands.background)):
        for path in paths:
            for left, right in zip(path.nodes, path.nodes[1:], strict=False):
                loads[edge_between(left, right)] += demand * path.share
    return LinkValues(tuple(loads[edge] for edge in EDGES))


def route_loads(costs: LinkValues, demands: Demands, background_via_source: bool = False) -> LinkValues:
    """관측 기반 예측에도 사용할 수 있는 정적 라우팅 함수."""
    return forwarding_loads(route_plan(costs, background_via_source), demands)


def blend_routes(old: Forwarding, new: Forwarding, fraction: float) -> Forwarding:
    """유효한 경로 비율들의 convex combination으로 수렴을 모델링한다."""
    if not 0 <= fraction <= 1:
        raise ContractError("fraction", "requires fraction in [0,1]")
    result: list[tuple[WeightedPath, ...]] = []
    for old_paths, new_paths in ((old.target, new.target), (old.background, new.background)):
        shares: dict[tuple[str, ...], float] = {}
        for paths, weight in ((old_paths, 1 - fraction), (new_paths, fraction)):
            for path in paths:
                shares[path.nodes] = shares.get(path.nodes, 0.0) + weight * path.share
        result.append(tuple(WeightedPath(nodes, share) for nodes, share in sorted(shares.items()) if share > 0))
    return Forwarding(result[0], result[1])
