"""평가기 전용 실제 상태; 제어기 입력으로 사용하지 않는다."""

from dataclasses import dataclass

from .routing import Forwarding, route_loads
from .types import DEFAULT_COSTS, Demands, LinkValues


@dataclass(frozen=True, slots=True)
class TruthSnapshot:
    """부하 안전·수렴 완료·원상복귀를 서로 구분한 실제 상태."""

    tick: int
    loads: LinkValues
    costs: LinkValues
    demands: Demands
    capacities: LinkValues
    forwarding: Forwarding
    pending_commands: int
    residual_fraction: float
    background_via_source: bool

    @property
    def max_utilization(self) -> float:
        return max(load / cap for load, cap in zip(self.loads.values, self.capacities.values, strict=True))

    @property
    def loads_safe(self) -> bool:
        return self.max_utilization <= 1.0 + 1e-12

    @property
    def settled(self) -> bool:
        return self.pending_commands == 0 and self.residual_fraction == 0.0

    @property
    def safe(self) -> bool:
        return self.loads_safe and self.settled

    @property
    def restored(self) -> bool:
        return self.costs == DEFAULT_COSTS and self.settled

    @property
    def default_safe(self) -> bool:
        """현재 실제 수요를 기본 경로에 보낸 반사실적 정상 비용 안전성."""
        loads = route_loads(DEFAULT_COSTS, self.demands, self.background_via_source)
        return all(load <= cap + 1e-12 for load, cap in zip(loads.values, self.capacities.values, strict=True))
