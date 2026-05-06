package com.anm.collector.client;

import com.anm.collector.dto.NetworkMetricDto;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;

/**
 * Mock SNMP Agent(Flask, 포트 5001)에 HTTP GET으로 메트릭을 요청한다.
 * 실장비 연동 시 이 클래스만 실제 SNMP 구현체로 교체하면 된다.
 */
@Component
public class SnmpClient {

    private final HttpClient http;
    private final ObjectMapper mapper;
    private final String baseUrl;

    public SnmpClient(
            @Value("${anm.snmp.base-url}") String baseUrl,
            ObjectMapper mapper
    ) {
        this.baseUrl = baseUrl;
        this.mapper  = mapper;
        this.http    = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(3))
                .build();
    }

    /** 전체 노드 메트릭 수집 (GET /metrics). */
    public List<NetworkMetricDto> fetchAllMetrics() {
        HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + "/metrics"))
                .GET()
                .timeout(Duration.ofSeconds(5))
                .build();
        try {
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() != 200) {
                throw new RuntimeException("SNMP agent returned HTTP " + resp.statusCode());
            }
            return mapper.readValue(resp.body(), new TypeReference<>() {});
        } catch (Exception e) {
            throw new RuntimeException("Failed to fetch metrics from SNMP agent", e);
        }
    }

    /** 단일 노드 메트릭 수집 (GET /metrics/{nodeId}). */
    public NetworkMetricDto fetchMetric(String nodeId) {
        HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(baseUrl + "/metrics/" + nodeId))
                .GET()
                .timeout(Duration.ofSeconds(5))
                .build();
        try {
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() != 200) {
                throw new RuntimeException("SNMP agent returned HTTP " + resp.statusCode());
            }
            return mapper.readValue(resp.body(), NetworkMetricDto.class);
        } catch (Exception e) {
            throw new RuntimeException("Failed to fetch metric for node " + nodeId, e);
        }
    }
}
