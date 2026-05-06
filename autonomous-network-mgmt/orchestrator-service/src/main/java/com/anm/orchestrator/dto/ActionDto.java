package com.anm.orchestrator.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

@JsonIgnoreProperties(ignoreUnknown = true)
public record ActionDto(
        String targetLink,
        int    newOspfCost,
        String agentType
) {}
