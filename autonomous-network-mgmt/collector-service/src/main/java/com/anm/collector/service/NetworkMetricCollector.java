package com.anm.collector.service;

import com.anm.collector.client.SnmpClient;
import com.anm.collector.dto.NetworkMetricDto;
import com.anm.collector.kafka.MetricPublisher;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class NetworkMetricCollector {

    private static final Logger log = LoggerFactory.getLogger(NetworkMetricCollector.class);

    private final SnmpClient snmpClient;
    private final MetricPublisher publisher;

    public NetworkMetricCollector(
            SnmpClient snmpClient,
            MetricPublisher publisher
    ) {
        this.snmpClient = snmpClient;
        this.publisher  = publisher;
    }

    /**
     * collectNetworkMetrics():
     * 1. Mock SNMP Agent에서 원시 메트릭 수집
     * 2. Kafka topic "network.metrics" 발행 (원시값 그대로)
     *
     * 정규화는 AI 엔진(api_server)이 자기 규약(bw/1000, lat/200)으로 수행한다.
     * 과거에는 MetricNormalizer로 정규화 후 발행했으나, AI 엔진 규약과 방향/스케일이
     * 달라(lat: 1-x/500 vs x/200) 이상 판정과 에이전트 관측이 모두 왜곡되는 문제가
     * 있어 원시값 발행으로 통일했다.
     */
    @Scheduled(fixedDelayString = "${anm.collector.interval-ms}")
    public void collectNetworkMetrics() {
        log.info("Collecting network metrics...");
        try {
            List<NetworkMetricDto> rawMetrics = snmpClient.fetchAllMetrics();

            for (NetworkMetricDto raw : rawMetrics) {
                publisher.publish(raw);
                log.debug("Collected & published: {}", raw);
            }

            log.info("Published {} metrics to Kafka", rawMetrics.size());
        } catch (Exception e) {
            log.error("Metric collection failed: {}", e.getMessage());
        }
    }
}
