package com.anm.orchestrator.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;

/**
 * Mock SSH → Mininet API 클라이언트.
 * 실장비 연동 시 이 클래스만 Netmiko 구현체(Python sidecar)로 교체.
 * 현재는 Mock SNMP Agent의 PUT /ospf/costs/{link} 엔드포인트를 재사용.
 */
@Component
public class MininetClient {

    private static final Logger log = LoggerFactory.getLogger(MininetClient.class);

    private final HttpClient http;
    private final ObjectMapper mapper;
    private final String baseUrl;

    public MininetClient(
            @Value("${anm.mininet.base-url}") String baseUrl,
            ObjectMapper mapper
    ) {
        this.baseUrl = baseUrl;
        this.mapper  = mapper;
        this.http    = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .build();
    }

    /**
     * setOspfCost(link, cost) → Mininet net.setLinkParam() 호출에 해당.
     * Mock: PUT /ospf/costs/{link} {"cost": cost}
     */
    public boolean setOspfCost(String link, int cost) {
        try {
            String json = mapper.writeValueAsString(Map.of("cost", cost));
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(baseUrl + "/ospf/costs/" + link))
                    .PUT(HttpRequest.BodyPublishers.ofString(json))
                    .header("Content-Type", "application/json")
                    .timeout(Duration.ofSeconds(5))
                    .build();
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            boolean ok = resp.statusCode() == 200;
            if (ok) {
                log.info("OSPF cost updated: link={} cost={}", link, cost);
            } else {
                log.warn("OSPF update failed: link={} status={}", link, resp.statusCode());
            }
            return ok;
        } catch (Exception e) {
            log.error("MininetClient.setOspfCost failed: {}", e.getMessage());
            return false;
        }
    }
}
