package com.anm.orchestrator.service;

import com.anm.orchestrator.client.AiEngineClient;
import com.anm.orchestrator.client.MininetClient;
import com.anm.orchestrator.dto.ActionDto;
import com.anm.orchestrator.dto.NetworkMetricDto;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

@Service
public class OrchestrationService {

    private static final Logger log = LoggerFactory.getLogger(OrchestrationService.class);

    private final AiEngineClient aiEngine;
    private final MininetClient  mininet;
    private final boolean        anomalyCheckEnabled;

    // 최근 수집된 메트릭 버퍼 (N 노드 한 라운드 분)
    private final List<NetworkMetricDto> metricBuffer = new CopyOnWriteArrayList<>();
    private static final int BUFFER_SIZE = 4; // 노드 수

    public OrchestrationService(
            AiEngineClient aiEngine,
            MininetClient mininet,
            @Value("${anm.orchestrator.anomaly-check-enabled}") boolean anomalyCheckEnabled
    ) {
        this.aiEngine            = aiEngine;
        this.mininet             = mininet;
        this.anomalyCheckEnabled = anomalyCheckEnabled;
    }

    /**
     * Kafka "network.metrics" 소비 → analyzeAnomaly → executeOrchestration.
     */
    @KafkaListener(topics = "${anm.kafka.topic}", groupId = "${spring.kafka.consumer.group-id}")
    public void onMetric(NetworkMetricDto metric) {
        log.debug("Received metric: {}", metric);

        metricBuffer.add(metric);

        // 이상 감지: 단일 노드 기준
        if (anomalyCheckEnabled && aiEngine.isAnomaly(metric)) {
            log.warn("Anomaly detected on node {}", metric.nodeId());
        }

        // 버퍼가 찼을 때(한 라운드 완성) 오케스트레이션 실행
        if (metricBuffer.size() >= BUFFER_SIZE) {
            List<NetworkMetricDto> snapshot = new ArrayList<>(metricBuffer);
            metricBuffer.clear();
            executeOrchestration(snapshot);
        }
    }

    /**
     * executeOrchestration():
     * 1. AI Engine에서 action 수신
     * 2. MininetClient.setOspfCost(link, cost) 호출
     */
    public void executeOrchestration(List<NetworkMetricDto> metrics) {
        try {
            ActionDto action = aiEngine.decideAction(metrics);
            log.info("Action decided: link={} cost={} agent={}",
                    action.targetLink(), action.newOspfCost(), action.agentType());

            boolean applied = mininet.setOspfCost(action.targetLink(), action.newOspfCost());
            if (applied) {
                log.info("Orchestration applied: link={} newCost={}",
                        action.targetLink(), action.newOspfCost());
            } else {
                log.warn("Orchestration failed to apply action");
            }
        } catch (Exception e) {
            log.error("Orchestration error: {}", e.getMessage());
        }
    }
}
