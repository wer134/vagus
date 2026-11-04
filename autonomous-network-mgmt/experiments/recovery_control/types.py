"""관측에 공개되는 불변 값과 네트워크 상수."""

from collections.abc import Iterator
from dataclasses import dataclass
from math import isfinite
from typing import Final

EDGES: Final[tuple[str, ...]] = ("a-b", "a-c", "b-d", "c-d", "b-c")
NODES: Final[tuple[str, ...]] = ("a", "b", "c", "d")


@dataclass(frozen=True, slots=True)
class ContractError(ValueError):
    """명세 경계를 위반한 필드와 이유."""

    field: str
    reason: str

    def __str__(self) -> str:
        return f"{self.field}: {self.reason}"


@dataclass(frozen=True, slots=True)
class LinkValues:
    """EDGES 순서의 양방향 합산 링크 값."""

    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.values) != len(EDGES):
            raise ContractError("values", "requires one value per edge")
        if any(not isfinite(value) or value < 0 for value in self.values):
            raise ContractError("values", "requires finite nonnegative values")

    def __getitem__(self, edge: str) -> float:
        if edge not in EDGES:
            raise ContractError("edge", f"unknown edge {edge}")
        return self.values[EDGES.index(edge)]

    def items(self) -> Iterator[tuple[str, float]]:
        return iter(zip(EDGES, self.values, strict=True))

    def with_value(self, edge: str, value: float) -> "LinkValues":
        """한 링크만 교체한 새 벡터를 반환한다."""
        _ = self[edge]
        return LinkValues(tuple(value if name == edge else old for name, old in self.items()))


DEFAULT_COSTS: Final = LinkValues((10.0, 10.0, 10.0, 10.0, 10.0))
DEFAULT_CAPACITIES: Final = LinkValues((60.0, 220.0, 60.0, 220.0, 80.0))


@dataclass(frozen=True, slots=True)
class Demands:
    """a→d target와 b→c background의 현재 전송량."""

    target: float
    background: float

    def __post_init__(self) -> None:
        if any(not isfinite(value) or value < 0 for value in (self.target, self.background)):
            raise ContractError("demands", "requires finite nonnegative values")


@dataclass(frozen=True, slots=True)
class CostCommand:
    """한 링크의 양방향 라우팅 비용 변경 요청."""

    edge: str
    value: float

    def __post_init__(self) -> None:
        if self.edge not in EDGES:
            raise ContractError("edge", f"unknown edge {self.edge}")
        if not isfinite(self.value) or self.value <= 0:
            raise ContractError("cost", "requires finite positive value")


DETOUR_COMMAND: Final = CostCommand("a-b", 100.0)
RESTORE_COMMAND: Final = CostCommand("a-b", 10.0)


@dataclass(frozen=True, slots=True)
class CommandAck:
    """접수만 확인하며 적용 또는 수렴 완료를 의미하지 않는다."""

    command: CostCommand
    acknowledged_at: int


@dataclass(frozen=True, slots=True)
class Observation:
    """샘플 시점의 측정치; 실제 수요와 수렴 내부 상태는 포함하지 않는다."""

    sampled_at: int
    arrived_at: int
    loads: LinkValues
    costs: LinkValues
    capacities: LinkValues
    reported_demands: Demands
    background_via_source: bool

    def __post_init__(self) -> None:
        if self.sampled_at < 0 or self.arrived_at < self.sampled_at:
            raise ContractError("observation_time", "requires 0 <= sampled_at <= arrived_at")
        if any(value <= 0 for value in self.costs.values + self.capacities.values):
            raise ContractError("observation", "costs and capacities must be positive")

    @property
    def max_utilization(self) -> float:
        return max(load / cap for load, cap in zip(self.loads.values, self.capacities.values, strict=True))
