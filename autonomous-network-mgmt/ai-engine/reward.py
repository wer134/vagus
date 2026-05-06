"""보상 함수 R = w_lat*lat_term + w_tput*tput_term - w_pen*sla_violation.

모든 항이 [0,1] 범위로 정규화되어 스케일 안정성 확보.
"""

W_LAT  = 0.4
W_TPUT = 0.4
W_PEN  = 0.2

SLA_LATENCY_MS  = 50.0   # ms 초과 시 SLA 위반
SLA_PACKET_LOSS = 0.01   # 1% 초과 시 SLA 위반

_LAT_REF = SLA_LATENCY_MS   # 정규화 기준 (50ms → term=0.5)


def compute_reward(
    latencies: list[float],
    bandwidths: list[float],
    packet_losses: list[float],
) -> float:
    """
    latencies    : 노드별 지연 ms (원시값)
    bandwidths   : 노드별 가용 대역폭 Mbps
    packet_losses: 노드별 패킷손실율 0~1
    """
    n = max(len(latencies), 1)

    avg_tput    = sum(bandwidths)   / n
    # 최악 노드 기준으로 지연/손실 평가 (어느 노드든 SLA 위반 시 페널티)
    max_latency = max(latencies)
    max_loss    = max(packet_losses)
    avg_latency = sum(latencies)    / n   # 보상 기울기는 평균 기반

    # 지연 항: 1/(1 + lat/50ms) → [0,1], SLA 기준(50ms)에서 0.5
    lat_term  = 1.0 / (1.0 + avg_latency / _LAT_REF)

    # 처리량 항: Mbps / 1000 → [0,1]
    tput_term = min(avg_tput / 1000.0, 1.0)

    # SLA 위반 패널티: 최악 노드 기준 (어느 노드든 위반 시 패널티)
    sla_viol = float(
        max_latency > SLA_LATENCY_MS or max_loss > SLA_PACKET_LOSS
    )

    return float(W_LAT * lat_term + W_TPUT * tput_term - W_PEN * sla_viol)
