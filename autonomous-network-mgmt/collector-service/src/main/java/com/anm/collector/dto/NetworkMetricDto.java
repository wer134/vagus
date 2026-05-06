package com.anm.collector.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

@JsonIgnoreProperties(ignoreUnknown = true)
public record NetworkMetricDto(
        String nodeId,
        double bandwidth,    // Mbps
        double latency,      // ms
        double packetLoss,   // 0.0 ~ 1.0
        long   timestamp     // epoch ms
) {}
