package com.anm.collector.kafka;

import com.anm.collector.dto.NetworkMetricDto;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;
import org.springframework.stereotype.Component;

import java.util.concurrent.CompletableFuture;

@Component
public class MetricPublisher {

    private static final Logger log = LoggerFactory.getLogger(MetricPublisher.class);

    private final KafkaTemplate<String, NetworkMetricDto> kafkaTemplate;
    private final String topic;

    public MetricPublisher(
            KafkaTemplate<String, NetworkMetricDto> kafkaTemplate,
            @Value("${anm.kafka.topic}") String topic
    ) {
        this.kafkaTemplate = kafkaTemplate;
        this.topic = topic;
    }

    /** nodeId를 파티션 키로 사용해 순서 보장. */
    public void publish(NetworkMetricDto metric) {
        CompletableFuture<SendResult<String, NetworkMetricDto>> future =
                kafkaTemplate.send(topic, metric.nodeId(), metric);

        future.whenComplete((result, ex) -> {
            if (ex != null) {
                log.error("Failed to publish metric for node {}: {}", metric.nodeId(), ex.getMessage());
            } else {
                log.debug("Published metric: node={} offset={}",
                        metric.nodeId(), result.getRecordMetadata().offset());
            }
        });
    }
}
