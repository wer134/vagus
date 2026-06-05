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
    private final MetricNormalizer normalizer;
    private final MetricPublisher publisher;

    public NetworkMetricCollector(
            SnmpClient snmpClient,
            MetricNormalizer normalizer,
            MetricPublisher publisher
    ) {
        this.snmpClient = snmpClient;
        this.normalizer = normalizer;
        this.publisher  = publisher;
    }

    /**
     * collectNetworkMetrics():
     * 1. Mock SNMP Agent에서 원시 메트릭 수집
     * 2. ENI 정규화
     * 3. Kafka topic "network.metrics" 발행
     */
    @Scheduled(fixedDelayString = "${anm.collector.interval-ms}")
    public void collectNetworkMetrics() {
        log.info("Collecting network metrics...");
        try {
            List<NetworkMetricDto> rawMetrics = snmpClient.fetchAllMetrics();

            for (NetworkMetricDto raw : rawMetrics) {
                NetworkMetricDto normalized = normalizer.normalize(raw);
                publisher.publish(normalized);
                log.debug("Collected & published: {}", normalized);
            }

            log.info("Published {} metrics to Kafka", rawMetrics.size());
        } catch (Exception e) {
            log.error("Metric collection failed: {}", e.getMessage());
        }
    }
}
