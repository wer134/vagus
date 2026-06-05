package com.anm.orchestrator.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

@JsonIgnoreProperties(ignoreUnknown = true)
public record NetworkMetricDto(
        String nodeId,
        double bandwidth,
        double latency,
        double packetLoss,
        long   timestamp
) {}
