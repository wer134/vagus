package com.anm.collector.service;

import com.anm.collector.dto.NetworkMetricDto;
import org.springframework.stereotype.Component;

/**
 * ENI(Expected Normal Interval) 기반 정규화.
 * 각 지표를 [0, 1] 범위로 변환한다.
 *
 * [파이프라인에서 제외됨 — 2026-09-06]
 * 이 정규화 규약(lat: 1 - x/500, "클수록 좋음")은 AI 엔진의 관측 규약
 * (NetworkEnv: lat x/200, "클수록 나쁨")과 방향·스케일이 달라, 정규화된 값을
 * Kafka로 발행하면 (1) 에이전트가 지연 심각도를 반대로 읽고 (2) /anomaly의
 * SLA 규칙(latency > 50ms)이 영구히 발화하지 않는 문제가 있었다.
 * 현재 수집 계층은 원시값을 발행하고 정규화는 AI 엔진이 책임진다.
 * 이 클래스는 실장비 연동 시 단위 변환 참조용으로만 남겨둔다.
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
