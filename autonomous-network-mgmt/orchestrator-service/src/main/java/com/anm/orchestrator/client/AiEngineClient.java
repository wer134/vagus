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

    /** 행동 결정 요청 (POST /action). */
    public ActionDto decideAction(List<NetworkMetricDto> metrics) {
        try {
            List<Double> bws      = metrics.stream().map(NetworkMetricDto::bandwidth).toList();
            List<Double> lats     = metrics.stream().map(NetworkMetricDto::latency).toList();
            List<Double> costs    = metrics.stream().map(m -> 10.0).toList(); // 기본값

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
