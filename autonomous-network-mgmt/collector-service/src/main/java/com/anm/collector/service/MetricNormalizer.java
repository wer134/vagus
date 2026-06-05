package com.anm.collector.service;

import com.anm.collector.dto.NetworkMetricDto;
import org.springframework.stereotype.Component;

/**
 * ENI(Expected Normal Interval) 기반 정규화.
 * 각 지표를 [0, 1] 범위로 변환하여 AI 엔진 입력에 맞춘다.
 */
@Component
public class MetricNormalizer {

    // 정규화 기준값 (SLA 기준 및 물리적 상한)
    private static final double MAX_BANDWIDTH   = 1000.0;  // Mbps
    private static final double MAX_LATENCY     = 500.0;   // ms
    private static final double MAX_PACKET_LOSS = 1.0;     // 100 %

    /**
     * 원시 메트릭을 정규화된 복사본으로 변환한다.
     * bandwidth: 높을수록 좋으므로 그대로 /MAX 정규화
     * latency, packetLoss: 낮을수록 좋으므로 역방향 정규화 (1 - val/MAX)
     */
    public NetworkMetricDto normalize(NetworkMetricDto raw) {
        double normBw   = clamp(raw.bandwidth()   / MAX_BANDWIDTH);
        double normLat  = clamp(1.0 - raw.latency()     / MAX_LATENCY);
        double normLoss = clamp(1.0 - raw.packetLoss()  / MAX_PACKET_LOSS);

        return new NetworkMetricDto(
                raw.nodeId(),
                normBw,
                normLat,
                normLoss,
                raw.timestamp()
        );
    }

    private static double clamp(double v) {
        return Math.max(0.0, Math.min(1.0, v));
    }
}
