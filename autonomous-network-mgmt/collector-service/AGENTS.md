# COLLECTOR SERVICE

Scheduled HTTP metric collection and raw Kafka publication for the Java control path.

## WHERE TO LOOK

Java paths below are relative to `src/main/java/com/anm/collector/`.

| Task | Location | Notes |
|------|----------|-------|
| Startup / scheduling | `CollectorApplication.java` | `@EnableScheduling` activates the polling method |
| Collection cycle | `service/NetworkMetricCollector.java` | Fetch all nodes, publish each raw DTO |
| Simulator transport | `client/SnmpClient.java` | HTTP GET `/metrics` and `/metrics/{nodeId}` |
| Kafka send / callbacks | `kafka/MetricPublisher.java` | `nodeId` key; asynchronous completion |
| Wire contract | `dto/NetworkMetricDto.java` | JSON record shared by shape with orchestrator |
| Historical normalization | `service/MetricNormalizer.java` | Reference implementation outside the live collection path |
| Runtime configuration | `src/main/resources/application.yml` | Polling, endpoint, topic, producer serialization |

## DATA CONTRACT

- Publish raw values: `nodeId` string, `bandwidth` Mbps, `latency` ms, `packetLoss` fraction `0.0 to 1.0`, `timestamp` epoch milliseconds.
- Keep DTO field names and types compatible with `../orchestrator-service/src/main/java/com/anm/orchestrator/dto/NetworkMetricDto.java`; the records live in separate packages.
- Both DTOs ignore unknown JSON properties. A new field still needs an explicit consumer decision before it influences orchestration.
- Normalization belongs to the AI engine. Do not insert `MetricNormalizer` into the publisher path: its latency/loss inversion differs from the AI observation contract.
- `SnmpClient` is an HTTP adapter to the simulator, despite its name; there is no actual SNMP exchange here.

## COLLECTION AND DELIVERY

- `@Scheduled(fixedDelayString = "${anm.collector.interval-ms}")` waits after each collection method completes; it is not a fixed-rate clock.
- `fetchAllMetrics()` completes before publication begins. A failed HTTP request, non-200 response, or JSON decode aborts that collection cycle and is logged by the collector.
- The HTTP client has a 3-second connect timeout and each request has a 5-second timeout; transport changes belong in `SnmpClient`.
- Kafka records use `nodeId` as the key. Preserve that per-node routing when changing the producer.
- `publish()` does not await broker completion. Asynchronous send failures are logged in its callback; the collector's published-count log is not delivery confirmation.
- JSON type headers are disabled (`spring.json.add.type.headers: false`); do not couple the consumer to the collector's Java package name.

## CONFIGURATION

- Edit `anm.snmp.base-url` for the simulator endpoint, `anm.collector.interval-ms` for polling delay, and `anm.kafka.topic` for the destination topic.
- Kafka connection and serializer settings live under `spring.kafka`; the HTTP listening port is `server.port`.
- Topic or serialization changes must agree with the orchestrator consumer configuration.
