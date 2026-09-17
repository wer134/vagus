# autonomous-network-mgmt — 프로젝트 설명서

> 대상 경로: `C:\autonomous-network-mgmt\autonomous-network-mgmt\`
> 작성일: 2026-09-06 · 소스 코드를 직접 읽고 정리한 기술 문서
> 2026-09-09 갱신: 코드 감사(`AUDIT_2026-09-09.md`) 결과 반영 — 시뮬레이터 시계, RCA, 지지 버퍼, 실패 처리

리포지토리 루트의 `README.md`가 "무엇을 주장하는 프로젝트인가"(연구 포지셔닝)를 다룬다면,
이 문서는 **"실제 코드가 어떻게 구성되고 무엇이 어디까지 동작하는가"**를 정리한다.
검증 결과의 한계와 코드에서 확인된 이슈도 그대로 기록한다.

---

## 1. 한 줄 요약

4개 라우터로 구성된 가상 네트워크에서 **혼잡·라우팅 위조(OSPF LSA)·트래픽 공격(DDoS/포트스캔)** 을
AI로 탐지하고, ETSI ZSM/ENI 폐쇄 루프(OODA)로 **사람 개입 없이 OSPF cost를 자동 조정해 복구**하는
연구용 프로토타입.

핵심 아이디어는 두 계층의 결합이다.

| 계층 | 담당 | 구현 |
| --- | --- | --- |
| **Analytics** (Orient) | SLA 위반 감지 + 근본 원인 링크 추정 | `anomaly_detector.diagnose()` + `api_server._root_cause_analysis()` |
| **Intelligence** (Decide) | 어떤 링크에 어떤 cost를 줄지 결정 | MAML few-shot 에이전트 `FewShotAgent` |

Analytics가 고신뢰로 근본 원인을 짚으면 MAML의 결정을 **override** 한다. 실험적으로 이 override가
성능의 대부분을 만들어낸다(§6 참고).

---

## 2. 리포지토리 구조

```
autonomous-network-mgmt/
├── simulation/                     # [Python] 가상 네트워크 + Mock SNMP
│   ├── metric_generator.py         #   링크 스트레스 모델 · 메트릭/공격 시뮬레이션 (228 L)
│   ├── mock_snmp_agent.py          #   Flask REST API (:5001) + AI Engine 프록시 (162 L)
│   ├── topology.py                 #   Mininet 4-라우터 토폴로지 (현 파이프라인 미사용, 84 L)
│   ├── Dockerfile                  #   ubuntu22.04 + mininet + OVS
│   └── requirements.txt            #   flask
│
├── ai-engine/                      # [Python] 탐지 · 의사결정 · 폐쇄 루프
│   ├── api_server.py               #   FastAPI (:8000) — OODA 루프 본체 (614 L)
│   ├── anomaly_detector.py         #   IsolationForest 이상탐지 + 링크 진단 + 보안 탐지 (183 L)
│   ├── ospf_security.py            #   OSPF LSA 위조/인증 검증 엔진 (284 L)
│   ├── reward.py                   #   보상 함수 (45 L)
│   ├── environment/network_env.py  #   gymnasium 환경 (223 L)
│   ├── agents/
│   │   ├── baseline_drl.py         #   PPO 베이스라인 (stable-baselines3)
│   │   ├── few_shot_agent.py       #   MAML (순수 PyTorch 구현, 213 L)
│   │   └── *.pt / *.zip            #   학습된 체크포인트 (커밋되어 있음)
│   ├── create_dummy_models.py      #   파이프라인 테스트용 더미 가중치 생성
│   └── requirements.txt
│
├── collector-service/              # [Java 17 / Spring Boot 3.3] 메트릭 수집 (:8081)
├── orchestrator-service/           # [Java 17 / Spring Boot 3.3] 행동 실행 (:8082)
│
├── experiments/                    # [Python] 실험 스크립트 + 결과
│   ├── run_experiment.py           #   학습/평가/샘플효율 파이프라인 (392 L)
│   ├── stress_test.py              #   OODA 루프 TTR 스트레스 테스트
│   ├── ablation_study.py           #   Analytics / MAML / Combined 절제 실험
│   ├── persistent_buffer_test.py   #   지지 버퍼 장기 유지 실험
│   ├── cicddos_loader.py           #   CICDDoS2019 CSV → 6피처 윈도우 변환
│   ├── validate_security_detector.py  # 실데이터 DDoS 탐지 검증
│   ├── CICDDOS2019_SETUP.md        #   데이터셋 준비 가이드
│   ├── experiment_report.md        #   실험 보고서
│   └── results/                    #   *.json / *.csv / 학습곡선 png
│
├── data/cicddos2019/Syn.csv        # 실데이터 (.gitignore 처리 — 커밋 대상 아님)
├── dashboard.html                  # 정적 데모 대시보드 (루트 index.html과 동일 파일)
└── docker-compose.yml              # Kafka/Zookeeper/PostgreSQL/Redis/Simulation
```

Python 코드는 총 약 3,400 라인, Java는 2개 서비스 각 4~6 클래스 규모다.

---

## 3. 런타임 아키텍처

### 3.1 프로세스와 포트

| 프로세스 | 포트 | 기술 | 역할 |
| --- | --- | --- | --- |
| `mock_snmp_agent.py` | 5001 | Flask | 가상 장비 역할. 메트릭 제공 + OSPF cost 수신 + 공격/혼잡 주입 |
| `api_server.py` | 8000 | FastAPI | 탐지 · 의사결정 · OODA 루프 · 보안 API |
| `collector-service` | 8081 | Spring Boot | 5초 주기 SNMP 수집 → Kafka `network.metrics` 발행 |
| `orchestrator-service` | 8082 | Spring Boot | Kafka 소비 → AI Engine 질의 → OSPF cost 적용 |
| Kafka / Zookeeper | 9092 / 2181 | Confluent 7.6.1 | 메트릭 스트림 |
| PostgreSQL / Redis | 5432 / 6379 | 16-alpine / 7.2-alpine | 저장소 (compose에 정의, 현 코드에서 적극 사용되진 않음) |

### 3.2 두 갈래의 제어 경로

이 프로젝트에는 **동일 목적의 제어 루프가 두 벌** 존재한다. 실험·논문 결과는 전부 (B)에서 나온다.

**(A) Java 마이크로서비스 경로** — 표준적인 스트리밍 파이프라인 데모

```
mock_snmp_agent(:5001) --GET /metrics--> collector-service(:8081)
      --Kafka network.metrics--> orchestrator-service(:8082)
      --POST /anomaly, /action--> ai-engine(:8000)
      --PUT /ospf/costs/{link}--> mock_snmp_agent(:5001)
```

**(B) Python OODA 폐쇄 루프 경로** — 실제 성능 실험이 사용하는 경로

```
POST /auto-step  (ai-engine 내부에서 한 사이클 전체를 수행)
  Observe  : GET :5001/metrics, /ospf/costs
  Orient   : diagnose() + _root_cause_analysis()
  Decide   : FewShotAgent.adapt_and_predict()  (+ Analytics override)
  Act      : PUT :5001/ospf/costs/{link}
  Evaluate : ModelPerformanceTracker
```

`experiments/stress_test.py`, `ablation_study.py`, `persistent_buffer_test.py`는 모두 (B)의
`/auto-step`을 호출하고, `run_experiment.py`는 HTTP 없이 `NetworkEnv`를 직접 돌린다(local_mode).

---

## 4. 핵심 모듈 상세

### 4.1 `simulation/metric_generator.py` — 시뮬레이터의 심장

이 파일이 없으면 프로젝트 전체가 성립하지 않는다. **링크 스트레스 모델**로 "행동 → 관측" 피드백을 만든다.

- 상태: 링크 6개 각각의 스트레스 `s ∈ [0,1]` (0=정상, 1=완전 혼잡)
- **라우팅 (2026-09-09, ROADMAP B-1)**: 노드 쌍별 수요를 OSPF cost 최단경로(ECMP)로 실어 링크 부하를
  계산한다(`SIM_VERSION = 3`). 이전의 "cost 역수 비율" 근사에서는 우회가 공짜였다 —
  `set_routing_mode("cost_inverse")`로 구 모델을 재현할 수 있다.
- **시간은 `tick()`으로만 흐른다 (2026-09-09).** `get_*_metrics()`는 순수 조회다. 이전에는 조회마다
  스트레스가 갱신되어 관측 횟수가 곧 시뮬레이션 시간이었다 (`AUDIT_2026-09-09.md` P1).
  `reset_state(seed=)`로 노이즈를 재현할 수 있다.
- 갱신식: `s_next = clip(s × 0.90 + load_s + cong_s + N(0, 0.015))`
  - `load_s` : OSPF cost 역수 기반 트래픽 비율 × 0.01 × 링크수 → **cost를 올리면 트래픽이 빠진다**
  - `cong_s` : 혼잡 주입 상태이고 `cost < 100`이면 0.50, `cost >= 100`이면 **0** (우회 성공 → 격리)
- 노드 메트릭 = 인접 링크 스트레스 평균 → 대역폭 950→10 Mbps, 지연 3→180 ms, 손실 0→4%
- `inject_congestion(link)`은 스트레스를 즉시 0.95로 올려 확실한 SLA 위반을 유발한다

즉 **"혼잡 링크의 cost를 100 이상으로 올린다"가 정답 행동**이고, 그 뒤 스트레스는 0.9^N으로 자연 감소한다.
`experiment_report.md`가 말하는 "물리적 시정수 τ = -1/ln(0.9) ≈ 9.5 step"이 여기서 나온다.

공격 시뮬레이션(`inject_attack`)은 보안 피처를 난수로 생성한다.

| 상태 | syn_ratio | unique_src_count | pkt_rate |
| --- | --- | --- | --- |
| 정상 | 0.02–0.10 | 10–80 | 100–1,000 |
| `ddos` | 0.45–0.85 | 50–300 | 15,000–50,000 |
| `portscan` | 0.20–0.40 | 800–2,000 | 500–3,000 |

### 4.2 `simulation/mock_snmp_agent.py` — 가상 장비 REST API (:5001)

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/metrics`, `/metrics/{node}` | 노드 메트릭 |
| GET | `/metrics/security[/{node}]` | 보안 피처 포함 메트릭 |
| GET | `/ospf/costs` | 링크별 OSPF cost |
| PUT | `/ospf/costs/{link}` | cost 변경 (허용값 10/20/50/100/200) |
| POST/DELETE | `/debug/congestion/{link}` | 혼잡 주입·해제 |
| POST/DELETE | `/debug/attack/{type}`, `/debug/attack` | 공격 주입·해제 (`ddos` / `portscan`) |
| POST | `/debug/reset` | 에피소드 리셋 (body `{"seed": N}` 지원) |
| POST | `/debug/tick` | 시뮬레이션 시간 진행 (body `{"n": 1}`) — lockstep 모드에서 유일한 시간 진행 수단 |
| GET | `/debug/stress`, `/debug/state`, `/debug/attack-state` | 디버그 상태 (틱, 링크 스트레스, 혼잡 링크 등) |
| ANY | `/ai/<path>` | AI Engine(:8000) 프록시 (브라우저 CORS 우회용) |

`SIM_CLOCK` 환경변수: `lockstep`(기본, `/debug/tick`으로만 진행) / `realtime:<ms>`(백그라운드 스레드가
주기적으로 tick — Java collector·대시보드처럼 관측만 하는 클라이언트용).

### 4.3 `ai-engine/environment/network_env.py` — 강화학습 환경

- **상태 (14차원, 0~1 정규화)**: `[대역폭×4, 지연×4, OSPF cost×6]`
- **행동 (Discrete 30)**: `action = link_idx × 5 + cost_idx`, 링크 6개 × cost {10, 20, 50, 100, 200}
- `local_mode=True`(기본): HTTP 없이 `metric_generator` 모듈을 직접 호출 → 학습이 매우 빠름
- `local_mode=False`: `:5001` REST 호출 (평가/실서비스용). 관측 실패 시 예외 (가짜 정상값 없음)
- `step()` = 행동 적용 → `tick()` 1회 → 관측. 상수는 `ai-engine/topology.py`에서 import
- `train_links` 인자로 **학습 링크와 평가 링크를 분리**해 일반화를 측정한다
  - TRAIN = `r1-r2, r1-r3, r2-r3, r2-r4` / TEST = `r3-r4, r1-r4`

### 4.4 `ai-engine/reward.py`

```
R = 0.4 × 1/(1 + avg_latency/50ms)  +  0.4 × min(avg_bw/1000, 1)  −  0.2 × SLA_위반
```

SLA 기준: 지연 > 50 ms 또는 손실 > 1% (최악 노드 기준으로 패널티).

### 4.5 `ai-engine/agents/` — 두 에이전트

| | Baseline | Few-shot |
| --- | --- | --- |
| 알고리즘 | PPO (stable-baselines3) | MAML (순수 PyTorch 구현) |
| 네트워크 | MlpPolicy `[128, 64]` | `14 → 128 → 64 → 30` |
| 학습 | `total_timesteps=50,000` | meta_lr 3e-4, fast_lr 0.02, 500 iters, task 4개/iter, inner 3 step |
| 내부 손실 | PPO clip | REINFORCE + baseline (advantage 정규화) |
| 추론 특징 | 고정 정책 | `adapt_and_predict()` — 지지 버퍼로 inner-loop 적응 후 결정 |

> 참고: `requirements.txt`에 `learn2learn==0.2.0`이 있지만 실제 MAML은 `torch.autograd.grad`로
> 직접 구현되어 있고 learn2learn은 import되지 않는다.

### 4.6 `ai-engine/anomaly_detector.py` — 3종 탐지기

1. **`AnomalyDetector`** — IsolationForest(contamination 0.05). 샘플 50개 이상부터 학습(최근 200개 유지,
   10샘플마다 재학습). 학습 전에는 SLA 규칙만으로 판정한다. **Java 경로 `/anomaly`에서만 판정에 쓰이며
   폐쇄 루프 `/auto-step`은 `update()`만 호출하고 판정에는 쓰지 않는다** (2026-09-09 확인).
2. **`diagnose(metrics, ospf_costs)`** — 규칙 기반 진단. SLA 위반 노드 → 인접 링크 = `suspected_links`,
   그중 cost < 100인 것 = `unhandled_links`. severity는 지연 100 ms / 손실 5% 초과 시 `critical`.
   **`root_cause_analysis(diag, ospf_costs)`** (2026-09-09에 api_server에서 이동·개정): 위반 노드 전부에
   인접한 미대응 링크 → 근본 원인; 이미 대응된 링크가 위반 노드 전부를 덮으면 → `None`(회복 대기);
   그 외 폴백 `(-공유 노드 수, cost)`. 개정 전에는 2번째 사이클부터 정상 링크를 지목했다 (AUDIT P2).
3. **`SecurityAnomalyDetector`** — 6피처(대역폭·지연·손실·syn_ratio·unique_src_count·pkt_rate)
   IsolationForest + 임계치 규칙 결합.

   | 피처 | 임계치 | 판정 |
   | --- | --- | --- |
   | `unique_src_count` | ≥ 500 | portscan |
   | `pkt_rate` | ≥ 10,000 | ddos |
   | `syn_ratio` | ≥ 0.30 | ddos |

   `is_threat = 임계치 초과 OR IsolationForest 이상`. 호출 순서는 `detect()` → `update()`
   (2026-09-09: 판정 대상을 학습셋에 넣기 전에 판정). 레이블 없이 모든 샘플로 학습하므로 공격이
   지속되면 공격을 '정상'으로 학습하는 한계가 있다.

### 4.7 `ai-engine/ospf_security.py` — OSPF 라우팅 보안

**비인증 경로 `check_lsa()` — 콘텐츠 이상 3규칙**

| 규칙 | 조건 |
| --- | --- |
| `unknown_router` | 라우터 ID가 `{r1, r2, r3, r4}`에 없음 |
| `seq_jump` | Δseq < 0 이거나 > 50 |
| `lsa_flood` | 5초 윈도우 내 3회 이상 재발송 |

**인증 경로 `check_lsa_authenticated()` — RFC 2328 Appendix D / RFC 5709**

| 규칙 | 조건 |
| --- | --- |
| `auth_none` | 인증 없는 평문 LSA |
| `auth_downgrade` | 단순 비밀번호(type 1)로 강제 다운그레이드 |
| `auth_digest_mismatch` | HMAC 다이제스트 불일치 |
| `auth_replay` | `crypto_seq <= 마지막 수신값` |
| `auth_weak_algo` | 인증은 유효하나 정책(SHA256) 대비 MD5 사용 |

정책은 `REQUIRED_AUTH_TYPE = AUTH_CRYPTO_SHA256`, key_id 2개(1=폐기예정, 2=활성)로 키 회전을 모사한다.
**인증을 통과해도 위 3규칙을 다시 적용**하는 defense-in-depth 구조다 — 키가 유출된 공격자가
유효 서명으로 비정상 시퀀스를 보내는 경우를 `seq_jump`로 잡기 위함.

세 가지 우회 시나리오가 함수로 구현되어 있고, 파일을 직접 실행하면 자가 테스트가 돈다.

```bash
python ai-engine/ospf_security.py     # replay / downgrade / key-compromise / weak-algo 4종 assert
```

| 시나리오 | 기대 결과 |
| --- | --- |
| `simulate_replay_attack` | 첫 수신 정상 → 동일 패킷 재전송은 `auth_replay`로 차단 |
| `simulate_auth_downgrade` | `auth_none`으로 즉시 차단 |
| `simulate_key_compromise_forge` | **인증은 통과** → `seq_jump`에서만 탐지 (인증만으론 부족하다는 것을 보이는 데모) |

### 4.8 `ai-engine/api_server.py` — OODA 폐쇄 루프 본체

**`POST /auto-step`** 한 번이 폐쇄 루프 한 사이클이다.

1. **Observe** — `POST :5001/debug/tick`(사이클당 1틱) 후 메트릭·cost 수집, 14차원 관측 벡터 구성.
   **수집 실패 시 HTTP 503** — 2026-09-09 이전에는 '정상' 기본값으로 대체해 장비 다운이 "조치 불필요"로
   보고됐다 (AUDIT C1).
2. **Orient** — `diagnose()`(SLA 규칙) + `root_cause_analysis()` (§4.6)
3. **Decide** — 지지 버퍼 ≥ 4면 `adapt_and_predict(adapt_steps=3)`, 아니면 meta-init 직접 사용
   - **Analytics override**: 근본 원인 링크에 SLA 위반 노드가 **2개 이상** 인접하면
     MAML 결정을 무시하고 `root_cause @ cost=100`으로 교체
   - `disable_maml=true`(analytics-only)에서 근본 원인이 `None`이면 **무행동**(회복 대기)
   - 지지 버퍼에는 실제로 실행된 행동의 전이만 들어간다 (`_prev_action=None` 처리, AUDIT P6)
4. **Act** — `PUT :5001/ospf/costs/{link}`. 응답 `act`에 `applied`/`changed`/`prev_cost`/`error`
5. **Evaluate** — `ModelPerformanceTracker`(window 20)가 보상·TTR·SLA 위반율을 추적하고
   `avg_ttr > 50` 또는 `sla_viol_rate > 0.7`이면 `needs_retrain=true`를 반환

`/auto-step`·`/reset-buffer`·`/action`은 `threading.Lock`으로 직렬화된다.

응답에는 각 단계 결과와 함께 사람이 읽는 `reasoning_chain` 문자열이 포함된다.
`?disable_analytics=true`, `?disable_maml=true` 쿼리로 절제 실험 모드를 켤 수 있다.

**주요 엔드포인트**

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/health`, `/model-status` | 상태 · 모델 자가진단 |
| GET | `/diagnose` | Orient 결과(근본 원인 포함) 단독 조회 |
| POST | `/auto-step` | **OODA 1 사이클** |
| POST | `/anomaly`, `/action` | Java 서비스가 호출하는 단건 API |
| POST | `/ospf/lsa-check`, `/ospf/lsa-check-auth` | LSA 검사(비인증 / 인증) |
| GET | `/ospf/security-status`, `/ospf/auth-config` | 알림 이력 · 인증 정책 |
| POST | `/debug/fake-lsa`, `/debug/ospf-attack/{replay,downgrade,key-compromise}` | 공격 데모 |
| POST | `/security/detect` | 트래픽 공격 탐지 + OpenFlow 차단 룰(시뮬레이션) 반환 |
| GET | `/security/status`, `/live-results` | 통합 보안 상태 · 실시간 결과 |
| POST | `/reset-buffer` | 지지 버퍼 초기화 |

### 4.9 Java 서비스

**collector-service (:8081)** — `@Scheduled(5000ms)` → `SnmpClient.fetchAllMetrics()` →
`MetricNormalizer` → `MetricPublisher`가 nodeId를 파티션 키로 Kafka `network.metrics`에 발행.

**orchestrator-service (:8082)** — `@KafkaListener`로 소비, 노드 4개가 모이면
`AiEngineClient.decideAction()` → `MininetClient.setOspfCost()`.
`anm.orchestrator.use-few-shot` 설정으로 PPO/MAML을 전환한다(기본 false).

두 클라이언트(`SnmpClient`, `MininetClient`) 모두 "실장비 연동 시 이 클래스만 교체" 지점으로 설계되어 있다.

2026-09-09: `isAnomaly()` 실패는 예외로 전파되고(이전엔 `false`), 판정 불가 노드가 있으면 라운드를 스킵하며,
전 노드 정상이면 행동하지 않는다(행동 공간에 NO-OP이 없어 매 라운드 행동하면 정상 cost를 계속 흔들었다).
JPA/PostgreSQL 의존성은 제거되어 PostgreSQL 없이 기동된다. 시뮬레이터를 `SIM_CLOCK=realtime:<ms>`로
띄워야 collector가 보는 메트릭이 변한다.

### 4.10 `dashboard.html`

Chart.js 기반 단일 파일 대시보드. **네트워크 호출이 전혀 없다** — 메트릭은 JS에서 자체 생성하고
실험 결과는 `EMBEDDED_SUMMARY` / `EMBEDDED_EFFICIENCY` 상수로 하드코딩되어 있다.
GitHub Pages용 정적 데모이며 실행 중인 시스템의 상태를 보여주지는 않는다
(루트의 `index.html`과 바이트 단위로 동일한 파일).

---

## 5. 실행 방법

### 5.1 인프라 (선택 — Java 경로를 쓸 때만 필요)

```bash
docker compose up -d zookeeper kafka postgres redis
```

`simulation` 서비스는 Mininet 때문에 `privileged: true` + `network_mode: host`가 필요해 Linux에서만
정상 동작한다. Windows/WSL에서는 아래처럼 Flask 에이전트를 직접 띄우는 편이 간단하다.

### 5.2 최소 구성으로 돌리기 (권장)

```bash
# 1) 가상 장비 (터미널 1)  — 실험용 lockstep 시계(기본). Java/대시보드 데모는 SIM_CLOCK=realtime:1000
cd simulation && pip install -r requirements.txt && python mock_snmp_agent.py     # :5001

# 2) AI 엔진 (터미널 2)
cd ai-engine && pip install -r requirements.txt && python api_server.py           # :8000

# 3) 폐쇄 루프 한 사이클 확인
curl -X POST http://127.0.0.1:8000/auto-step
```

모델 체크포인트가 없다면 `python ai-engine/create_dummy_models.py`로 더미를 만들어 파이프라인만
먼저 검증할 수 있다(성능은 무의미).

`ai-engine/requirements.txt`는 2026-09-09부터 하한만 고정한다(numpy ≥ 2, SB3 ≥ 2.8, torch ≥ 2.6).
커밋된 체크포인트가 그 환경에서 만들어졌기 때문이며, 이전 고정 버전(numpy 1.26)으로는
`ppo_network.zip`을 읽지 못한다. 체크포인트 로드 실패는 이제 `/health`의 `*_load_error`로 보고되고
엔진은 기동한다.

### 5.3 학습 · 평가

```bash
cd experiments
python run_experiment.py --train-baseline --timesteps 30000     # PPO 학습
python run_experiment.py --train-fewshot  --meta-iterations 200 # MAML 메타학습
python run_experiment.py --evaluate --episodes 30 --eval-links test
python run_experiment.py --all                                  # 전체 파이프라인
```

### 5.4 실험 (mock_snmp_agent + api_server 가 떠 있어야 함)

```bash
python experiments/stress_test.py --episodes 30
python experiments/ablation_study.py
python experiments/persistent_buffer_test.py
```

### 5.5 실데이터 보안 검증

```bash
# 데이터셋 준비는 experiments/CICDDOS2019_SETUP.md 참고 (UNB 가입 필요, 자동 다운로드 불가)
cd experiments
python validate_security_detector.py --csv-path ../data/cicddos2019/Syn.csv --benign-warmup 200
```

### 5.6 Java 서비스

```bash
cd collector-service    && ./mvnw spring-boot:run    # :8081
cd orchestrator-service && ./mvnw spring-boot:run    # :8082
```

---

## 6. 실험 결과 요약

> **2026-09-09 갱신**: 아래 §6.1~6.3의 수치는 **사이클당 시뮬레이터 2틱**으로 측정된 개정 전 값이다
> (측정 스크립트의 검증 조회가 시간을 한 번 더 진행시켰다 — `AUDIT_2026-09-09.md` P1). 1틱 기준
> 재측정치는 다음과 같고 상세는 AUDIT §3에 있다.
>
> | 실험 | 개정 전 (2틱) | **개정 후 (1틱)** | 부수 피해/ep |
> | --- | --- | --- | --- |
> | 스트레스 50 ep | TTR 3.78 / 100% | **6.84 / 100%** (TEST 6.69, TRAIN 7.11) | 0.80 |
> | 절제 analytics_only | 3.88 / 100% | **6.94 / 100%** | **0.00** |
> | 절제 maml_only | 12.32 / 24% | **13.80 / 14%** | 0.86 |
> | 절제 combined | 3.80 / 100% | **6.94 / 100%** | 0.86 |
> | 지속 버퍼 30 ep | 3.73 | **6.93** (초기 7.00 → 후기 6.87) | 0.73 |
> | 오프라인 PPO / MAML(재학습) / MAML(구) | 100.9 / — / 139.7 | **101.8 / 101.8 / 188.6** | — |
>
> 세 학습 정책(PPO, 재학습 MAML, 구 MAML)은 전부 상태 무관 상수 행동이다 (AUDIT P8).
>
> **더 최신 수치는 `ROADMAP.md` §6에 있다** — 최단경로 라우팅(`SIM_VERSION = 3`) 도입 후 재측정분이며,
> 폐쇄 루프 수치는 거의 동일하고(stress 6.86, 절제 7.08/13.86/6.90) 오프라인 평가는 학습/평가 링크
> 분리를 보장한 뒤 PPO 200.0 / 0%, MAML 101.81 / 50%로 갈렸다.
>
> **2026-09-06 갱신**: 아래 수치는 측정 경로가 두 가지이며 서로 비교할 수 없다.
> §6.1은 폐쇄 루프(`/auto-step`, Analytics 포함), §6.1b는 오프라인 정책 단독 평가다.

### 6.1 폐쇄 루프 — TTR(복구까지 걸린 OODA 사이클 수), 50 에피소드

| 시스템 | Avg TTR | 성공률 | RCA 정확도 | 일반화 격차 |
| --- | --- | --- | --- | --- |
| **MAML v2 + ZSM Analytics** | **3.78** | **100%** | **100%** | **0.0** |
| MAML v1 (Analytics override 이전) | 12.41 | 96.7% | N/A | N/A |

TTR 분포는 2:4% / 3:30% / 4:50% / 5:16%로 매우 안정적이며, 학습에 쓰지 않은 TEST 링크
(`r1-r4` 3.43, `r3-r4` 3.94)가 TRAIN 링크와 같은 수준이다.

### 6.1b 오프라인 평가 — 에이전트 정책 단독 (TEST 링크)

| 에이전트 | Avg TTR | 성공률(TTR<30) | 평균 보상 |
| --- | --- | --- | --- |
| Baseline PPO (미학습·랜덤, 30 ep) | 200.0 | 0% | 59.7 |
| **Baseline PPO (50k steps 학습, 50 ep)** | **100.9** | **50%** | 91.1 |
| MAML (Analytics 미적용, 50 ep) | 139.7 | 32% | 78.0 |

**같은 조건에서 학습된 PPO가 MAML보다 빠르다.** 기존 README의 "Baseline 대비 98.1% 단축"은
미학습 랜덤 정책과의 비교였고 측정 경로도 달라 철회됐다. §6.2 절제 실험과 종합하면
복구 성능의 동인은 MAML이 아니라 Analytics(RCA) 계층이다.
(출처: `results/offline_eval_trained_ppo.json`, 2026-09-06)

### 6.2 절제 실험 — 성능의 출처

`results/ablation_study.json` (50 에피소드/모드, 2026-05-20 실행)

| 모드 | Avg TTR | 성공률 |
| --- | --- | --- |
| `analytics_only` | 3.88 | 100% |
| `maml_only` | 12.32 | 24% |
| `combined` | 3.80 | 100% |

**결론: 성능의 핵심 동인은 Analytics(근본 원인 분석) 계층이다.** MAML 단독은 성공률 24%에 그치고,
Analytics만으로도 100% 복구가 된다. 결합해도 TTR은 거의 같다.

### 6.3 지속 버퍼 실험 (30 에피소드)

| 구간 | Avg TTR | RCA 정확도 | 적응 스텝/ep |
| --- | --- | --- | --- |
| 초기 1–15 | 3.73 | ~73% | 3.07 |
| 후기 16–30 | 3.73 | ~100% | 2.93 |

2차 행동이 초기 `r1-r2@200`(meta-init 기본값)에서 후기 `r2-r3@10`(사실상 no-op)로 바뀐다 —
"Analytics가 1스텝을 제대로 처리했으면 추가 간섭하지 않는다"를 학습한 것으로 해석됐으나,
**2026-09-09 재실행에서는 30/30 에피소드의 2차 행동이 `r3-r4@100`(상수)** 이라 이 해석은 지지되지 않는다.

### 6.4 CICDDoS2019 실데이터 검증 — 부분 개선, 여전히 미달

UNB CICDDoS2019 `Syn.csv`를 1초 윈도우 8,699개(BENIGN 3,813 / 공격 4,886)로 집계해
`SecurityAnomalyDetector`에 그대로 흘려보낸 결과:

| 지표 | 기존 집계 | 신규 집계 (2026-09-06) | 자명한 베이스라인 |
| --- | --- | --- | --- |
| Precision | 0.65 | 0.58 | 0.56 (= base rate) |
| Recall | **0.12** | **0.54** | 1.00 |
| F1 | 0.20 | **0.56** | **0.72** |

`pkt_rate`를 `Flow Packets/s` 합산으로 교체해 recall이 4.5배 올랐다 — 아래 진단이 옳았다는
확인이다. 그러나 F1 0.56은 "전부 공격 예측"(0.72)에 못 미치고 정확도(52.1%)도
always-attack(56.2%)보다 낮아, **탐지기의 실데이터 유용성은 아직 입증되지 않았다.**
남은 최대 누수는 `Flow Duration == 0`이라 전송률 계산이 불가해 제외되는 플로우
283,076개(6.6%)이며, SYN flood의 단발 패킷이 여기 해당한다.

**원인 진단 (임계치 문제가 아님):**

1. 이 배포본의 `SYN Flag Count` 컬럼이 공격 레이블 플로우에서도 거의 항상 0 →
   `syn_ratio` 피처가 사실상 무의미
2. `pkt_rate` / `unique_src_count`를 "플로우 시작 시각" 기준으로 1초 윈도우 집계하는 방식이
   시뮬레이션이 가정한 "초당 패킷 전송률"과 의미가 어긋남 (긴 플로우의 패킷이 시작 윈도우에만 집계)
3. 2,500 윈도우로 임계치를 그리드서치한 결과 세 피처 모두 "최적" 임계치가 "전부 공격으로 예측"과
   동일 → **임계치 재조정으로 해결 불가**
4. rolling F1 학습곡선(`results/cicddos_validation_nowarmup_curve.png`)이 시간이 지나도
   베이스레이트(F1 0.72)를 회복하지 못함 → cold-start가 아니라 **구조적 피처 불일치**

해법은 피처 추출 방식 자체의 재설계(raw pcap 기반 초당 패킷수, 또는 플로우 듀레이션에 걸친 분산 집계)다.

---

## 7. 읽는 사람이 알아야 할 한계와 주의점

시뮬레이션 결과를 그대로 성능 주장으로 옮기기 전에 확인해야 할 것들이다.

**측정 방법론**

- ~~Baseline PPO의 TTR 200.0은 "미학습" 모델 기록~~ → **해결 (2026-09-06)**: PPO를 50,000 스텝
  학습시켜 재평가한 결과 학습된 PPO(100.9)가 MAML(139.7)보다 빨랐다. "98.1% 단축" 주장은
  철회됐고 README에 정정이 명시됐다. 미학습 체크포인트는 `ppo_random_baseline.zip`으로 보존.
- ~~`sample_efficiency.json`의 측정 조건 불명~~ → **해결 (2026-09-06)**: 결과 JSON에
  `_condition` 키로 "오프라인 경로, Analytics override 없음"을 기록하고 README에도 각주를 달았다.
  근거 없던 "500배 샘플 효율" 문구는 삭제.
- ~~절제 실험 수치가 문서(15 ep)와 결과 파일(50 ep)에서 다름~~ → **해결 (2026-09-06)**:
  README와 `experiment_report.md`를 `results/ablation_study.json`(50 ep) 기준으로 통일하고
  출처(n, 실행일)를 명시했다.
- **남은 것**: `results/summary.json`의 baseline 항목은 여전히 30 에피소드 미학습 실행분이다
  (fewshot은 50 에피소드). 같은 표에 인용할 때 주의.

**측정 방법론 — 2026-09-09 감사 (`cowork/AUDIT_2026-09-09.md`)**

- 시뮬레이션 시간이 관측 호출로 흘러 실험 스크립트의 검증 조회가 사이클당 2틱을 만들었고, 보고된
  TTR(3.78~3.88)이 실제 사이클 수의 약 절반이었다 → **수정**(tick 분리). 이 문서 §6의 폐쇄 루프 수치는
  전부 2틱 기준이며, 1틱 기준 재측정치는 AUDIT §3에 있다.
- RCA가 2번째 사이클부터 정상 링크를 지목했고 "RCA 정확도 100%"는 첫 사이클만 잰 값이었다 → **수정**.
  부수 피해 지표(`wasted_actions`)와 전 사이클 정확도(`rca_all_cycles_ok`)를 추가했다.
- MAML 학습 롤아웃이 argmax(탐색 없음)였고 지지 버퍼에 실행하지 않은 행동이 기록됐다 → **수정**, 재학습.
- 관측 실패가 '정상'으로 대체됐다 → **수정** (503).

**환경**

- 모든 메트릭은 `metric_generator.py`의 난수 + 스트레스 모델 산출물이다. 실제 패킷도, 실제 SNMP도,
  실제 OSPF 데몬도 없다. `topology.py`(Mininet)는 존재하지만 현재 실험 파이프라인은 사용하지 않는다
  (2026-09-06에 docstring으로 명시).
- "정답 행동"(혼잡 링크 cost ≥ 100)이 시뮬레이터에 명시적으로 코딩되어 있어, 규칙 기반 Analytics가
  100% 정확한 것은 어느 정도 예정된 결과다. §6.2의 절제 실험이 이를 정량적으로 보여준다.
- OpenFlow 차단 룰은 **반환만 되고 적용되지 않는다** (`"note": "시뮬레이션 OpenFlow 차단 룰 (데모)"`).
- `dashboard.html`은 라이브 데이터를 읽지 않는 정적 데모다(§4.10) — 2026-09-06에 페이지
  상단과 `EMBEDDED_SUMMARY` 주석으로 명시했다.

**코드에서 확인된 이슈**

- ~~`AiEngineClient.decideAction()`이 `ospfCosts`를 노드 수(4개) 고정값으로 채워 12차원 관측이
  만들어져 `/action`이 항상 실패~~ → **해결 (2026-09-06)**. 뿌리는 세 결함이 아니라
  "수집 계층과 AI 엔진의 정규화 규약 불일치" 하나였다:
  - collector가 원시 메트릭을 발행하도록 변경 (`MetricNormalizer`의 `1 - lat/500`은
    `NetworkEnv`의 `lat/200`과 방향·스케일이 반대였다 — 클래스는 참조용으로 남김)
  - orchestrator가 `MininetClient.fetchOspfCosts()`로 실제 cost 6개를 `LINK_ORDER`대로 전달,
    노드도 `NODE_ORDER`로 정렬, 조회 실패 시 임의값 대신 라운드 스킵
  - `_obs_from_payload()`가 `_metrics_to_obs()와` 동일 규약으로 정규화하고, 차원 불일치는
    조용한 오작동 대신 **HTTP 400**으로 거부
  - 부수 효과로 `/anomaly`가 정상 동작한다 — 정규화된 값(0~1)에 `latency > 50ms` SLA 규칙을
    적용해 영구히 발화하지 않던 문제가 함께 사라졌다
  - 검증: `mvn compile` 양쪽 통과, `/action` 200 / 잘못된 차원 400 / `/anomaly` true 확인.
    **Kafka 전체 경로는 Docker 미기동으로 미검증.**
- ~~`learn2learn`이 설치 목록에만 있고 쓰이지 않는다~~ → **해결**: `requirements.txt`에서 제거.
- PostgreSQL/Redis는 compose와 `application.yml`에 설정되어 있으나 실제 영속화 코드는 없다
  (미사용임을 compose에 주석으로 명시).

**미검증 영역**

- 다중 동시 위협(혼잡 2개 이상, 혼잡 + 공격 동시) 시나리오
- 포트스캔 탐지 — CICDDoS2019에 포트스캔이 없어 실데이터 검증이 불가능했다(실패가 아니라 데이터셋 특성)
- OSPF 보안은 전부 규칙 기반 — ML 기반 시퀀스 패턴 학습은 미적용

---

## 8. 표준 매핑 (ETSI ZSM / ENI)

| 표준 조항 | 구현 |
| --- | --- |
| ZSM 002 Clause 3.1.1 — Closed-loop automation | `api_server.auto_step()` |
| ZSM 002 Clause 3.1.1.2 — Analytics Service | `diagnose()` + `_root_cause_analysis()` |
| ZSM 002 Clause 3.1.1.3 — Intelligence Service | `FewShotAgent.adapt_and_predict()` |
| ZSM 002 Clause 3.1.1.4 — AI Model Evaluation | `ModelPerformanceTracker` |
| ENI 007 — Experiential Networked Intelligence | 지지 버퍼 누적을 통한 inner-loop few-shot 적응 |
| RFC 2328 Appendix D / RFC 5709 | `ospf_security.verify_auth()` (MD5 / HMAC-SHA256) |

---

## 9. 개발 이력 (커밋 기준)

| 시점 | 내용 |
| --- | --- |
| 2026-05-06 | Mininet 토폴로지 · SNMP 시뮬레이션 · MAML/PPO 에이전트 골격, 데모 대시보드 |
| 2026-05-20 | 절제 실험(50 ep) 추가 — Analytics 계층이 핵심 동인임을 최초 실증 |
| 2026-06-05 | origin 리포지토리 이전 (`autonomous-network-mgmt` → `autonomous-network`) |
| 2026-06-18 | OSPF LSA 위조 탐지 + 트래픽 기반 보안 탐지 도입, README를 위협 인텔리전스로 재포지셔닝 |
| 2026-06-20 | OSPF MD5/SHA256 인증 + 우회 시나리오 3종, CICDDoS2019 검증 파이프라인 구축 |
| 2026-06-21 | recall 0.12 원인 진단(피처 추출 방법론 문제) 및 학습곡선 시각화, 결과를 문서에 반영 |
| 2026-09-06 | 코드 감사 1차 (`FIX_PLAN.md` T1–T8): 피처 추출 재설계, 학습된 PPO 베이스라인, Java 경로 계약 정합화 |
| 2026-09-09 | 코드 감사 2차 (`AUDIT_2026-09-09.md` A1–A7): 시뮬레이터 시계 분리, RCA 개정, MAML 롤아웃 샘플링·재학습, 조용한 실패 제거, 전 실험 재측정 — 정책 붕괴(P8) 발견 |

---

## 10. 다음에 손댈 만한 것

(2026-09-09 정리 — 이전 목록의 2~4번은 §7에서 이미 해결된 항목이라 제거했다)

> 상세 고도화 계획(트랙·단계·완료 기준)은 `cowork/ROADMAP.md`에 있다.

1. **트래픽 보안 피처 추출 재설계** — `Flow Duration == 0` 플로우 처리, raw pcap 기반 초당 패킷수
   (임계치 조정으로는 불가능함이 §6.4에서 확인됨. 가장 우선순위 높은 과제)
2. **`SecurityAnomalyDetector`의 학습 방식** — 레이블 없이 모든 샘플로 학습해 지속 공격을 정상으로
   학습하는 구조, `contamination` 가정과 실데이터 base rate 불일치 (AUDIT P4)
3. **NO-OP 행동** — 행동 공간에 무행동이 없어 오프라인 평가/PPO 학습에서 정상 상태에도 매 스텝
   cost를 바꿔야 한다. 공개 시그니처(30 행동)를 바꾸는 결정이라 보류 (AUDIT P7)
4. 다중 동시 위협 시나리오 추가 (RCA 폴백 규칙이 실제로 검증되는 유일한 경우)
5. Kafka 전체 경로(A) 실기동 검증 (Docker)
6. 실제 SDN 컨트롤러(OpenDaylight/ONOS) 연동으로 OpenFlow 차단 룰 실제 적용
7. GNN 기반 상태 표현으로 대규모 토폴로지 확장
