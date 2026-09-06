package com.anm.orchestrator.client;

import com.anm.orchestrator.dto.ActionDto;
import com.anm.orchestrator.dto.NetworkMetricDto;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.Map;

@Component
public class AiEngineClient {

    /**
     * /action 관측 벡터의 순서 규약 — AI 엔진(api_server._metrics_to_obs / NetworkEnv)과
     * 동일해야 한다. 순서가 어긋나면 에이전트가 다른 링크/노드의 값으로 판단하게 된다.
     */
    public static final List<String> NODE_ORDER = List.of("r1", "r2", "r3", "r4");
    public static final List<String> LINK_ORDER = List.of(
            "r1-r2", "r1-r3", "r2-r3", "r2-r4", "r3-r4", "r1-r4");

    private final HttpClient http;
    private final ObjectMapper mapper;
    private final String baseUrl;
    private final boolean useFewShot;

    public AiEngineClient(
            @Value("${anm.ai-engine.base-url}") String baseUrl,
            @Value("${anm.orchestrator.use-few-shot}") boolean useFewShot,
            ObjectMapper mapper
    ) {
        this.baseUrl    = baseUrl;
        this.useFewShot = useFewShot;
        this.mapper     = mapper;
        this.http       = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .build();
    }

    /** 이상 감지 요청 (POST /anomaly). */
    public boolean isAnomaly(NetworkMetricDto metric) {
        try {
            Map<String, Object> body = Map.of(
                    "nodeId",     metric.nodeId(),
                    "bandwidth",  metric.bandwidth(),
                    "latency",    metric.latency(),
                    "packetLoss", metric.packetLoss()
            );
            String json = mapper.writeValueAsString(body);
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(baseUrl + "/anomaly"))
                    .POST(HttpRequest.BodyPublishers.ofString(json))
                    .header("Content-Type", "application/json")
                    .timeout(Duration.ofSeconds(5))
                    .build();
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            Map<?, ?> result = mapper.readValue(resp.body(), Map.class);
            return Boolean.TRUE.equals(result.get("isAnomaly"));
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * 행동 결정 요청 (POST /action).
     *
     * @param metrics   노드 4개의 원시 메트릭 (노드 누락 시 예외)
     * @param ospfCosts 링크 6개의 현재 OSPF cost (MininetClient.fetchOspfCosts() 결과)
     *
     * 관측 벡터는 [bw×4, lat×4, cost×6] = 14차원이며 NODE_ORDER/LINK_ORDER로 정렬해
     * 보낸다. 과거에는 cost를 노드 수만큼(4개) 고정값 10.0으로 보내 12차원이 만들어져
     * 에이전트 추론이 항상 실패했다 — 실제 cost 6개 전달로 수정됨.
     */
    public ActionDto decideAction(List<NetworkMetricDto> metrics, Map<String, Integer> ospfCosts) {
        try {
            Map<String, NetworkMetricDto> byNode = new java.util.HashMap<>();
            for (NetworkMetricDto m : metrics) {
                byNode.put(m.nodeId(), m);
            }
            List<Double> bws  = new java.util.ArrayList<>();
            List<Double> lats = new java.util.ArrayList<>();
            for (String node : NODE_ORDER) {
                NetworkMetricDto m = byNode.get(node);
                if (m == null) {
                    throw new IllegalArgumentException("메트릭에 노드 누락: " + node);
                }
                bws.add(m.bandwidth());
                lats.add(m.latency());
            }
            List<Double> costs = new java.util.ArrayList<>();
            for (String link : LINK_ORDER) {
                Integer c = ospfCosts.get(link);
                if (c == null) {
                    throw new IllegalArgumentException("OSPF cost에 링크 누락: " + link);
                }
                costs.add(c.doubleValue());
            }

            Map<String, Object> body = Map.of(
                    "bandwidths",  bws,
                    "latencies",   lats,
                    "ospfCosts",   costs,
                    "useFewShot",  useFewShot
            );
            String json = mapper.writeValueAsString(body);
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(baseUrl + "/action"))
                    .POST(HttpRequest.BodyPublishers.ofString(json))
                    .header("Content-Type", "application/json")
                    .timeout(Duration.ofSeconds(10))
                    .build();
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            return mapper.readValue(resp.body(), ActionDto.class);
        } catch (Exception e) {
            throw new RuntimeException("AI Engine action request failed", e);
        }
    }
}
