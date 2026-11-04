# ORCHESTRATOR SERVICE KNOWLEDGE BASE

## OVERVIEW

Kafka consumer that assembles four-router metric rounds, gates AI decisions on anomaly verdicts, and applies OSPF costs through the simulator HTTP API.

## WHERE TO LOOK

Java paths below are relative to `src/main/java/com/anm/orchestrator/`.

| Task | Location | Notes |
|------|----------|-------|
| Round assembly and action gating | `service/OrchestrationService.java` | `onMetric` collects; `executeOrchestration` fetches costs and acts |
| AI request contract | `client/AiEngineClient.java` | `/anomaly`, `/action`, `NODE_ORDER`, `LINK_ORDER` |
| Simulator reads and writes | `client/MininetClient.java` | GET `/ospf/costs`; PUT `/ospf/costs/{link}` |
| Kafka metric / AI action fields | `dto/NetworkMetricDto.java`, `dto/ActionDto.java` | Records ignore unknown JSON fields |
| Runtime defaults and deserialization | `src/main/resources/application.yml` | Path relative to service root |
| Python side of observation contract | `../ai-engine/topology.py`, `../ai-engine/api_server.py` | Node/link ordering and API normalization |

## ROUND AND ACTION CONTRACT

- `latestByNode` keeps the latest received metric for each node; repeated arrivals replace that node rather than advancing a message count.
- A round completes only after `r1`, `r2`, `r3`, and `r4` are present. The snapshot is assembled in that fixed order.
- Both metric and anomaly maps are cleared before the round's skip checks or action request. A skipped or failed round is not retained for replay.
- With anomaly checking enabled, each received metric triggers `/anomaly`; request failures remove that node's verdict.
- A missing per-node verdict means unavailable diagnosis, not healthy. If any required verdict is missing, skip the completed round.
- If all verdicts are present but none are anomalous, skip action. The policy action space has no NO-OP, so acting on every healthy round changes costs unnecessarily.
- Disabling `anm.orchestrator.anomaly-check-enabled` bypasses both anomaly gates, but still requires a complete metric round and live link costs.
- Fetch current OSPF costs before `/action`. A failed fetch skips action; a missing required link makes `decideAction` reject the request. Do not substitute guessed default costs.

## WIRE CONTRACT

- Send raw `bandwidths[4]`, `latencies[4]`, and `ospfCosts[6]` plus `useFewShot` to `/action`; the Python API normalizes the resulting 14-value observation.
- Node order: `r1`, `r2`, `r3`, `r4`.
- Link order: `r1-r2`, `r1-r3`, `r2-r3`, `r2-r4`, `r3-r4`, `r1-r4`.
- Keep Java `NODE_ORDER` / `LINK_ORDER` synchronized with `../ai-engine/topology.py`. Map iteration order and alphabetical link sorting are not the contract.
- `/anomaly` receives one node's raw `nodeId`, `bandwidth`, `latency`, and `packetLoss`; packet loss is not part of the `/action` observation.
- `MininetClient` is an HTTP adapter to the mock simulator, despite its name; it does not call Mininet or SSH. Actuation sends `{"cost": value}` and accepts HTTP 200 as success.

## CONFIGURATION AND CURRENT LIMITS

- `application.yml` defaults: service port `8082`; AI `http://localhost:8000`; simulator `http://localhost:5001`.
- Kafka defaults: `localhost:9092`, topic `network.metrics`, consumer group `orchestrator-group`, offset reset `latest`.
- Kafka JSON deserialization trusts `com.anm.*` and uses this service's `NetworkMetricDto` as its default value type.
- Anomaly checking defaults to `true`; `use-few-shot` defaults to `false`. Both client URLs and these flags are injected from configuration.
- HTTP calls are synchronous, with 5-second connect timeouts; requests use 5 seconds except `/action` at 10 seconds. Clients implement no retry loop.
- Current error handling logs/skips failed orchestration. `/action` parses the response without checking its HTTP status; `/anomaly` checks HTTP 200 but treats any value other than Boolean `true` as false.
- Latest values are chosen by receipt order, not metric timestamps; there is no freshness window or timestamp-aligned multi-node snapshot.
