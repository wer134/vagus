# PROJECT KNOWLEDGE BASE

이 문서는 초기 저장소 구조 안내다. 에이전트 작업 규칙은 루트 `AGENTS.md`를 따른다.

**Generated:** 2026-11-03 (Asia/Seoul)
**Commit:** a8a6cab
**Branch:** main

## OVERVIEW

Vagus is a four-router research simulator for threat detection and autonomous network recovery. Python supplies the simulator, FastAPI OODA loop, and MAML/PPO experiments; two Java 17 / Spring Boot 3.3 services supply an alternative Kafka control path.

## STRUCTURE

```text
vagus/
├── index.html                    # GitHub Pages copy of the application dashboard
├── dashboard_data.js             # Generated experiment data for Pages
└── autonomous-network-mgmt/      # Application working directory (APP below)
    ├── ai-engine/                # FastAPI, diagnosis, policies, Gymnasium environment
    ├── simulation/               # Deterministic metric model and Flask HTTP adapter
    ├── collector-service/        # Raw metric polling -> Kafka
    ├── orchestrator-service/     # Kafka -> AI decision -> simulator actuation
    ├── experiments/              # Training, evaluation, provenance, figures
    ├── scripts/                  # Aggregate self-tests and Node dashboard smoke
    ├── cowork/                   # Runbook, audits, fix plan, roadmap
    ├── dashboard.html            # Must match root index.html
    ├── dashboard_data.js         # Generated twin of root dashboard_data.js
    └── docker-compose.yml        # Infrastructure and Linux simulation container
```

## WHERE TO LOOK

Paths below are relative to `APP` unless explicitly prefixed with `vagus/`.

| Task | Location | Notes |
|------|----------|-------|
| Python closed loop / API | `ai-engine/api_server.py` | `/auto-step` is one OODA cycle |
| Observation/action contract | `ai-engine/topology.py` | Ordering and version constants; Java must agree |
| Simulation dynamics | `simulation/metric_generator.py` | In-process model used by experiments |
| Java streaming path | `collector-service/`, `orchestrator-service/` | Separate Maven projects, no aggregate POM |
| Reproduction / result claims | `experiments/`, `cowork/AUDIT_2026-09-09.md` | Check result metadata before comparing numbers |
| Dashboard data | `experiments/make_dashboard_data.py` | Writes both `dashboard_data.js` copies |
| Runtime instructions | `cowork/PROJECT_OVERVIEW.md` | Cross-check old examples against current scripts |
| Planned changes | `cowork/ROADMAP.md` | Plans are not evidence of implemented behavior |

## CODE MAP

Source/AST map; reference centrality unmeasured. At generation, codegraph had no index and Python/Java LSP servers were unavailable.

| Symbol | Location | Role |
|--------|----------|------|
| `auto_step`, `_auto_step_locked` | `ai-engine/api_server.py` | Serialized observe/diagnose/decide/act loop |
| `NetworkEnv` | `ai-engine/environment/network_env.py` | Local or HTTP-backed Gymnasium environment |
| `FewShotAgent`, `BaselineAgent` | `ai-engine/agents/` | MAML adaptation and PPO policy |
| `tick`, `reset_state` | `simulation/metric_generator.py` | Explicit simulation time and seeded state |
| `collectNetworkMetrics` | `collector-service/src/main/java/com/anm/collector/service/NetworkMetricCollector.java` | Scheduled raw metric publication |
| `onMetric` | `orchestrator-service/src/main/java/com/anm/orchestrator/service/OrchestrationService.java` | Assemble four-node rounds and gate action |
| `result_meta` | `experiments/_resultmeta.py` | Measurement provenance and contract versions |

## CONVENTIONS

- Python checks live in module `if __name__ == "__main__":` blocks. `cowork/FIX_PLAN.md` explicitly prohibits introducing pytest.
- `scripts/selftest.sh` is the aggregate check: module assertions, compileall, optional Node smoke, checkpoint probe, and optional Maven compile. Inspect skips; policy collapse is a warning, and checkpoint load failure is reported as a skip.
- No checked-in CI workflow, formatter/type-checker configuration, Maven wrapper, or Java test sources exist. Use installed `mvn`; old `./mvnw` examples do not work in this checkout.
- Python modules use working-directory imports and `sys.path` adjustments, not an installed package. In particular, both simulation and AI contain a different `topology.py`.
- `vagus/index.html` and `APP/dashboard.html` must remain byte-identical. Both generated data copies must also match.
- The dashboard is a static demonstration: synthetic live-looking telemetry plus saved experiment results, not live API monitoring.
- README and cowork documentation are predominantly Korean; preserve existing terminology and record corrections with their measurement context.

## ANTI-PATTERNS

- Do not hand-edit measured results or dashboard numbers; rerun the producing scripts. Preserve historical artifacts and checkpoint provenance.
- Do not present offline policy evaluation and `/auto-step` results as one comparable metric: analytics overrides and stopping conditions differ.
- Do not attribute rule-based recovery to MAML or claim held-out link success proves learning. The repository explicitly records policy collapse and unproven RL benefit.
- Do not assume the Compose stack starts the AI or Java services. PostgreSQL/Redis are declared infrastructure but unused by the current Java path.

## COMMANDS

Run these from `vagus/autonomous-network-mgmt`. Shell scripts require Bash; full reproduction uses Linux/Unix process tools. Python dependencies are in `ai-engine/requirements.txt` and `simulation/requirements.txt`; Java requires JDK 17 and Maven.

```bash
bash scripts/selftest.sh                    # All available checks; inspect skips
bash scripts/selftest.sh --quick --no-policy # Skip Maven and checkpoint probing
node scripts/dashboard_smoke.js             # Dependency-free Node smoke harness
mvn -f collector-service/pom.xml -q compile
mvn -f orchestrator-service/pom.xml -q compile
```

Run servers in separate terminals from their own directories: `python3 mock_snmp_agent.py` in `simulation/`, `python3 api_server.py` in `ai-engine/`, and `mvn spring-boot:run` in each Java service directory. Reproduction commands and their write/process side effects are in `experiments/AGENTS.md`.

## NOTES

- Python `/auto-step` directly controls the simulator; Java separately polls -> Kafka `network.metrics` -> orchestrator -> `/anomaly` and `/action` -> OSPF update.
- Defaults: simulator `5001`, AI `8000`, collector `8081`, orchestrator `8082`, Kafka `9092`. Java observation-only polling needs the simulator's realtime clock; deterministic experiments use lockstep.
- `simulation/topology.py` is a Linux/Mininet reference, not the active experiment backend. Compose simulation requires privileged Linux host networking.
- Debug/reset APIs are unauthenticated, CORS is permissive, and OSPF demo keys are hardcoded. This is a local research demo, not an externally deployable security boundary.
