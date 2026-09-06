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
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class OrchestrationService {

    private static final Logger log = LoggerFactory.getLogger(OrchestrationService.class);

    private final AiEngineClient aiEngine;
    private final MininetClient  mininet;
    private final boolean        anomalyCheckEnabled;

    // 노드별 최신 메트릭 — Kafka 파티션 간 도착 순서가 보장되지 않고 같은 노드가
    // 연속 도착할 수 있으므로, "개수 세기" 대신 "노드별 최신값 맵"으로 라운드를 구성한다.
    private final Map<String, NetworkMetricDto> latestByNode = new ConcurrentHashMap<>();

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

        latestByNode.put(metric.nodeId(), metric);

        // 이상 감지: 단일 노드 기준 (원시값 — AI 엔진이 SLA 규칙/IsolationForest로 판정)
        if (anomalyCheckEnabled && aiEngine.isAnomaly(metric)) {
            log.warn("Anomaly detected on node {}", metric.nodeId());
        }

        // 4개 노드가 모두 모이면(한 라운드 완성) 오케스트레이션 실행
        if (latestByNode.keySet().containsAll(AiEngineClient.NODE_ORDER)) {
            List<NetworkMetricDto> snapshot = new ArrayList<>();
            for (String node : AiEngineClient.NODE_ORDER) {
                snapshot.add(latestByNode.get(node));
            }
            latestByNode.clear();
            executeOrchestration(snapshot);
        }
    }

    /**
     * executeOrchestration():
     * 1. 현재 OSPF cost 6개 조회 (관측 벡터에 필요 — 실패 시 이번 라운드 스킵)
     * 2. AI Engine에서 action 수신
     * 3. MininetClient.setOspfCost(link, cost) 호출
     */
    public void executeOrchestration(List<NetworkMetricDto> metrics) {
        try {
            Map<String, Integer> ospfCosts = mininet.fetchOspfCosts();
            if (ospfCosts == null) {
                // 임의 기본값으로 채우면 틀린 관측으로 행동하게 된다 — 스킵이 안전.
                log.warn("OSPF cost 조회 실패 — 이번 오케스트레이션 라운드 스킵");
                return;
            }

            ActionDto action = aiEngine.decideAction(metrics, ospfCosts);
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
