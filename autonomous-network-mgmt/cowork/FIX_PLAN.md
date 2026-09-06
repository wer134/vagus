# 수정 작업 지시서 (autonomous-network-mgmt)

> **수행자**: Fable 5.1
> **작성일**: 2026-09-06
> **작업 대상**: `C:\autonomous-network-mgmt\autonomous-network-mgmt\`
> **선행 문서**: `cowork/PROJECT_OVERVIEW.md` (§7 "한계와 주의점"에서 이 지시서의 항목들이 도출됨)

이 문서는 코드 감사에서 확인된 문제를 **어떻게 고칠 것인가**에 대한 실행 지시서다.
각 태스크는 독립적으로 커밋 가능하며, 근거가 되는 파일·라인, 구체적 변경, 검증 방법, 완료 기준을 포함한다.

---

## 0. 시작 전 필독

### 0.1 환경

| 항목 | 값 |
| --- | --- |
| OS / 셸 | Windows 11, PowerShell 기본 (Bash도 사용 가능) |
| 리포지토리 루트 | `C:\autonomous-network-mgmt\` (git repo, 브랜치 `main`) |
| 프로젝트 루트 | `C:\autonomous-network-mgmt\autonomous-network-mgmt\` |
| Python 의존성 | `ai-engine/requirements.txt` (torch, sb3, gymnasium, sklearn, pandas, fastapi) |
| Java | 17 / Spring Boot 3.3, Maven |
| 실데이터 | `data/cicddos2019/Syn.csv` — **1.87 GB, 이미 존재**. `.gitignore` 대상이므로 **절대 커밋 금지** |

**실험 실행 시 필요한 프로세스** (T3, T4 검증에 필요)

```bash
# 터미널 1
cd autonomous-network-mgmt/simulation && python mock_snmp_agent.py     # :5001
# 터미널 2
cd autonomous-network-mgmt/ai-engine  && python api_server.py          # :8000
```

`experiments/run_experiment.py`는 `local_mode=True`라 위 두 프로세스 없이 동작한다.
`stress_test.py` / `ablation_study.py` / `persistent_buffer_test.py`는 **두 프로세스가 모두 필요**하다.

### 0.2 지켜야 할 원칙 (중요)

이 프로젝트의 소유자는 **"지표가 좋아 보이게 만드는 것"보다 "무엇이 사실인지"를 우선**한다.
README에 이미 recall 0.12 같은 불리한 결과가 정직하게 기록되어 있고, 그 기조를 유지해야 한다.

1. **임계치·하이퍼파라미터를 결과가 좋아 보이도록 조정하지 말 것.** 조정이 정당한 경우에도
   조정 사실과 조정 전/후 수치를 **둘 다** 기록한다.
2. **`experiments/results/*.json`의 측정값을 수기로 편집하지 말 것.** 스크립트 재실행으로만 갱신한다.
   예외: 측정값이 아닌 메타데이터 키(`_note`, `_condition` 등) 추가는 허용한다.
3. **개선이 있어도 기존의 "한계" 서술을 삭제하지 말 것.** 이전 수치와 병기하고 무엇이 달라졌는지 쓴다.
4. **결과가 나쁘면 나쁜 대로 보고할 것.** 실패한 시도도 문서에 남긴다 (README "개발 일지"에 이미 그런 항목이 있다).
5. **공개 시그니처를 깨지 말 것**: FastAPI 엔드포인트 경로/응답 스키마,
   `SecurityAnomalyDetector.update()/detect()`의 6개 인자, `NetworkEnv`의 14차원 관측 / 30 행동.
6. 테스트는 이 리포의 관례를 따른다 — pytest 도입 금지. 변경한 모듈의 `if __name__ == "__main__":`
   블록에 자가 테스트를 추가한다 (`ospf_security.py`, `cicddos_loader.py`가 이미 그 방식이다).
7. 커밋은 태스크 단위로, 기존 스타일(`feat:` / `fix:` / `docs:`)을 따른다.

### 0.3 태스크 목록

| ID | 우선순위 | 내용 | 대상 | 코드 변경 | 실행 시간 |
| --- | --- | --- | --- | --- | --- |
| **T1** | P0 | CICDDoS2019 피처 추출 재설계 (recall 0.12의 실제 해법) | `experiments/cicddos_loader.py` | 있음 | 30–60분 |
| **T2** | P0 | 학습된 PPO 베이스라인 확보 → 공정 비교 | `experiments/`, `results/summary.json` | 없음(실행) | 1–2시간 |
| **T3** | P0 | Ablation 수치 문서/파일 불일치 해소 | README, `experiment_report.md` | 문서 | 5분 or 40분 |
| **T4** | P1 | Java 제어 경로(A) 계약 정합화 — 현재 런타임 오류 | Java 2개 서비스 + `api_server.py` | 있음 | 1–2시간 |
| **T5** | P2 | `sample_efficiency.json` 측정 조건 명시 | `run_experiment.py`, README | 소 | 15분 |
| **T6** | P2 | 미사용 요소 정리 (learn2learn, topology.py, PG/Redis) | 여러 파일 | 소 | 15분 |
| **T7** | P2 | `dashboard.html`이 정적 데모임을 명시 | `dashboard.html`, `index.html` | 소 | 20분 |
| **T8** | P3 | 최종 문서 동기화 | README, `PROJECT_OVERVIEW.md` | 문서 | 20분 |

**의존관계**: T1 → T8, T2 → T3 → T8, T4는 독립. **T8은 반드시 마지막.**
P0 3건만 해도 이 프로젝트의 신뢰도 문제 대부분이 해소된다.

---

## T1. CICDDoS2019 피처 추출 재설계 (P0)

### 현상

`results/cicddos_validation_nowarmup.json`: precision 0.65 / **recall 0.12** / F1 0.20.
README는 원인을 "임계치 문제가 아니라 피처 추출 방법론 문제"로 진단했고, 그 진단은 **옳다**.

### 원인 (실데이터로 재확인 완료)

`data/cicddos2019/Syn.csv`의 앞 200,000행(Syn 199,968 / BENIGN 32)을 샘플링해 실측한 값:

| 항목 | 공격(Syn) | BENIGN |
| --- | --- | --- |
| `SYN Flag Count` 비영(non-zero) 비율 | **0.02 %** | 0 % |
| `Flow Duration` 중앙값 | 87 µs | 102,863,769 µs (≈ 103초) |
| 플로우당 총 패킷 수 중앙값 | 4 | 18 |
| **`Flow Packets/s` 중앙값** | **40,816** | **0.47** |

여기서 두 가지가 확정된다.

1. **`syn_ratio`는 이 배포본에서 죽은 피처다.** 공격 행의 99.98 %가 `SYN Flag Count == 0`.
   임계치 0.30은 영원히 발화하지 않는다.
2. **`pkt_rate` 집계 방식이 신호를 파괴하고 있다.** 현재 `cicddos_loader.py:79`는
   `pkt_rate = (total_fwd + total_bwd).sum() / window_sec`으로 계산한다.
   공격 플로우는 "4개 패킷 / 87 µs" 즉 **실제로는 40,816 pps**인데, 윈도우 단위로 패킷 수만
   합산하면 수백 pps로 축소된다 → 임계치 10,000을 넘지 못함 → recall 붕괴.
   반대로 BENIGN 플로우는 103초짜리인데 패킷 전량이 **시작 시각 한 윈도우에만** 귀속되어
   그 윈도우만 비정상적으로 높아진다 → false positive.

**그리고 해법이 데이터 안에 이미 있다.** CICFlowMeter가 계산해 둔 `Flow Packets/s` 컬럼은
공격 40,816 vs BENIGN 0.47로 5자릿수 분리력을 가진다. 지금 로더가 이 컬럼을 안 쓰고 있을 뿐이다.

### 수정 방침

**1단계에서는 `pkt_rate` 집계 방식만 고친다.** 피처 개수·이름·`SecurityAnomalyDetector`의
시그니처는 건드리지 않는다(§0.2-5). 피처를 추가하면 `metric_generator.get_security_metrics()`,
`api_server.SecurityMetricPayload`, 시뮬레이션 경로까지 연쇄 수정이 필요해 범위가 폭발한다.

### 구체적 변경 — `experiments/cicddos_loader.py`

**(1) 컬럼 별칭 추가** (`_COLUMN_ALIASES`, 21~28행)

```python
"flow_duration":   ["Flow Duration", " Flow Duration"],
"flow_packets_s":  ["Flow Packets/s", " Flow Packets/s"],
```

두 컬럼은 실제 CSV에 존재함을 확인했다(각각 9번, 23번 컬럼). 다만 `_REQUIRED_KEYS`에 넣어
**하드 실패로 만들지는 말 것** — 다른 배포본에는 없을 수 있다. 없으면 경고 후 기존 방식으로
폴백하고, 그 사실을 결과 JSON에 `aggregation_mode` 필드로 남긴다.

**(2) `_aggregate_window()` (73~101행)의 `pkt_rate` 산출 교체**

```python
# 기존 (플로우 시작 윈도우에 패킷 전량 귀속 — 신호 파괴)
pkt_rate = total_packets / window_sec

# 신규 (권장): 윈도우와 겹치는 플로우들의 실제 전송률 합
pkt_rate = Σ over flows in window of  clean(Flow Packets/s)
```

- `Flow Packets/s`에는 `inf` / `NaN`이 존재할 수 있다(`Flow Duration == 0`인 플로우).
  `pd.to_numeric(..., errors="coerce")` 후 `inf`는 **버리지 말고** `total_packets / (duration_us/1e6)`로
  재계산하거나, 재계산도 불가하면 제외하고 **제외한 플로우 수를 결과에 기록**한다. 조용히 0으로
  만들면 안 된다.
- `Flow Duration` 단위는 **마이크로초**다.

**(3) 윈도우 귀속 방식 (선택, 여력이 있으면)**

현재는 플로우를 시작 시각 윈도우 하나에만 귀속한다. 103초짜리 BENIGN 플로우가 한 윈도우에
몰리는 왜곡을 없애려면 `[start, start + flow_duration]` 구간에 걸쳐 겹침 비율로 분배한다.
구현이 커지므로 **(2)만으로 재측정한 뒤, 개선 폭을 보고 판단**한다.

**(4) `syn_ratio` 처리**

계산식은 그대로 두되, 로딩 시 공격 윈도우의 `syn_ratio` 분포를 집계해 결과 JSON의
`known_limitations`에 **실측 근거와 함께** 기록한다:
> "공격 윈도우의 SYN Flag Count 비영 비율 X.XX% — 이 배포본에서 syn_ratio는 탐지에 기여하지 않음"

**(5) 자가 테스트 갱신** (149~173행 `__main__`)

기존 5행 CSV 픽스처에 `Flow Duration` / `Flow Packets/s` 컬럼을 추가하고,
"짧은 고속 플로우(공격)가 있는 윈도우의 `pkt_rate`가 임계치 10,000을 넘는다"는 assert를 추가한다.
`Flow Duration == 0` 행을 한 줄 넣어 `inf` 처리 경로도 검증한다.

### 검증

```bash
cd autonomous-network-mgmt/experiments
python cicddos_loader.py                     # 자가 테스트 통과 확인

# 소규모 스모크 (전체 8,699 윈도우는 시간이 걸린다)
python validate_security_detector.py --csv-path ../data/cicddos2019/Syn.csv \
    --max-windows 500 --output cicddos_smoke.json

# 전체 재측정 — 기존 결과와 같은 조건(warmup 0)으로 비교 가능해야 함
python validate_security_detector.py --csv-path ../data/cicddos2019/Syn.csv \
    --benign-warmup 0 --output cicddos_validation_v2.json
```

**기존 파일 `cicddos_validation_nowarmup.json`을 덮어쓰지 말 것.** 새 이름으로 저장해 비교 가능하게 둔다.

### 결과 해석 시 반드시 확인할 것 (함정)

이 CSV는 **공격 비중이 99.98 %** 다. recall이 올라갔다고 좋아하기 전에:

- **"전부 공격으로 예측"하는 자명한 베이스라인의 F1은 0.72**다 (스크립트가 이미 이 값을 계산해
  그래프에 점선으로 그린다). 새 F1이 0.72를 넘지 못하면 **개선이 아니다.**
- precision이 0.99 근처로 나오는 것은 실력이 아니라 base rate 때문이다. 반드시 base rate를
  결과 문서에 병기한다.
- 판정이 사실상 "전부 공격"으로 수렴하지 않았는지 confusion matrix의 **TN(좌상단)** 을 본다.
  기존 결과는 TN=3,495였다. 새 결과에서 TN이 0에 가까워지면 그건 탐지가 아니라 포기다.

### 완료 기준 (DoD)

- [ ] `cicddos_loader.py` 자가 테스트 통과
- [ ] 전체 재측정 결과가 `results/cicddos_validation_v2.json`으로 저장됨 (기존 파일 보존)
- [ ] 결과 JSON에 `aggregation_mode`, base rate, 제외된 `inf` 플로우 수가 기록됨
- [ ] 학습곡선 PNG 재생성, 베이스레이트 점선 대비 위치가 육안으로 판별 가능
- [ ] README "성능 검증" 절에 **기존 수치와 신규 수치를 표로 병기**하고, 개선/미개선을 그대로 서술
- [ ] 임계치는 건드리지 않았음 (건드렸다면 그 사실과 전/후 수치를 모두 기록)

---

## T2. 학습된 PPO 베이스라인 확보 (P0)

### 현상

`results/summary.json`의 baseline 항목 주석:
> `"PPO without trained model — random policy, never resolves congestion"`

즉 README와 `experiment_report.md`가 말하는 **"Baseline PPO 대비 98.1 % TTR 단축"은
학습된 PPO가 아니라 랜덤 정책과의 비교**다. 이 상태로는 "MAML이 PPO보다 낫다"는 주장을 할 수 없다.

### 수정 방침

코드 수정 없음. **PPO를 실제로 학습시켜 같은 조건에서 재평가**하고, 결과가 무엇이든 그대로 기록한다.

### 단계

1. 기존 체크포인트 보존 — `ai-engine/agents/ppo_network.zip`을 `ppo_random_baseline.zip`으로 복사.
   (학습이 이 파일을 덮어쓴다: `baseline_drl.py:MODEL_PATH`)
2. 학습 실행 (SNMP 서버 불필요, `local_mode=True`):
   ```bash
   cd autonomous-network-mgmt/experiments
   python run_experiment.py --train-baseline --timesteps 50000
   ```
   `--timesteps`는 `baseline_drl.train()` 기본값 50,000에 맞춘다. 백그라운드 실행 권장.
3. 동일 조건 재평가 (MAML 평가와 같은 링크·에피소드 수):
   ```bash
   python run_experiment.py --evaluate --episodes 50 --eval-links test
   ```
4. `results/summary.json`이 갱신되면 baseline `note`가 실제 조건(학습 스텝 수, 날짜)을 담도록
   `run_experiment.py`의 `_stats()` 근처에서 note를 기록하게 하거나, 갱신 후 메타데이터 키로 추가한다.
5. **랜덤 정책 행도 남긴다.** README 표를 3행에서 4행으로:

   | 시스템 | Avg TTR | 성공률 |
   | --- | --- | --- |
   | Baseline PPO (미학습·랜덤) | 200.0 | 0 % |
   | Baseline PPO (50k steps 학습) | *측정값* | *측정값* |
   | MAML v1 (Analytics 미적용) | 12.41 | 96.7 % |
   | MAML v2 + ZSM Analytics | 3.78 | 100 % |

### 주의

- 학습된 PPO가 **여전히 나쁠 가능성이 높다.** `sample_efficiency.json`을 보면 30,000 스텝에서도
  TTR 200이 나온다. 그렇게 나오면 그대로 쓰고, 왜 그런지(행동공간 30차원 × 희소 보상 × 짧은
  에피소드) 한 줄 해석을 붙인다. **PPO를 좋아 보이게 하려고, 혹은 나빠 보이게 하려고
  하이퍼파라미터를 손대지 말 것.**
- MAML 쪽 수치(3.78)는 `/auto-step` 경로(Analytics override 포함)에서 나온 것이고, 이 평가는
  `run_experiment.py` 오프라인 경로다. **두 수치를 같은 표에서 비교하려면 경로가 다르다는 각주를
  반드시 달 것.** (이 함정이 T5의 근본 원인이기도 하다.)

### 완료 기준

- [ ] `ppo_random_baseline.zip` 백업 존재
- [ ] 학습 로그와 재평가 결과가 `results/`에 저장됨
- [ ] README 비교표에 랜덤/학습 두 행이 모두 존재하고, 어느 쪽과의 비교인지 명시됨
- [ ] "98.1 % 단축" 문구가 실제 비교 대상을 밝히도록 수정됨

---

## T3. Ablation 수치 불일치 해소 (P0)

### 현상

같은 실험의 수치가 세 곳에서 다르다.

| 출처 | analytics_only | maml_only | combined | n |
| --- | --- | --- | --- | --- |
| `README.md` "Ablation Study" 표 | 3.93 (100 %) | 13.13 (27 %) | 4.20 (100 %) | 15 |
| `experiment_report.md` §7.3 | 3.93 (100 %) | 13.13 (27 %) | 4.20 (100 %) | 15 |
| **`results/ablation_study.json`** | **3.88 (100 %)** | **12.32 (24 %)** | **3.80 (100 %)** | **50** |

문서는 15 에피소드 예비 실행분을, 저장된 결과 파일은 50 에피소드 실행분(2026-05-20)을 담고 있다.

### 수정 방침 — 둘 중 하나 선택

**방침 A (권장, 5분)**: 재실행 없이 **문서를 결과 파일 기준으로 교체**한다.
README와 `experiment_report.md` §7.3의 표를 `ablation_study.json`(50 ep) 수치로 바꾸고,
표 아래에 출처를 명시한다:
> 출처: `experiments/results/ablation_study.json` (50 에피소드/모드, 2026-05-20 실행)

기존 15 ep 수치는 각주로 남기거나 삭제한다(삭제해도 무방 — 같은 실험의 더 작은 표본이다).

**방침 B (40분)**: `python experiments/ablation_study.py --episodes 50`을 재실행해 최신 코드 기준으로
다시 측정한다. 서버 2개가 필요하고 약 35~40분 걸린다(50 ep × 3 모드 × 최대 15 스텝 × ≈0.8초 + 리셋 대기).
백그라운드 실행할 것. **T2에서 PPO 체크포인트를 덮어쓴 뒤에 돌리면 `maml_only` 수치가 달라질 수
있으므로**, B를 택한다면 T2 완료 후에 실행한다.

방침 A로 충분하다. 결론(Analytics가 핵심 동인)은 두 표본에서 동일하다.

### 완료 기준

- [ ] README·`experiment_report.md`·`results/ablation_study.json`의 수치가 일치
- [ ] 표 아래에 n과 실행 일자가 명시됨
- [ ] (방침 B 선택 시) 재실행 결과가 저장되고 이전 파일이 보존됨

---

## T4. Java 제어 경로(A) 계약 정합화 (P1)

### 현상 — 이 경로는 현재 **동작하지 않는다**

세 개의 결함이 겹쳐 있고, 뿌리는 **"정규화 규약이 계층마다 다르다"** 하나다.

**(a) 관측 차원 불일치 → 런타임 오류**

`AiEngineClient.java:67`
```java
List<Double> costs = metrics.stream().map(m -> 10.0).toList(); // 기본값
```
노드 4개를 순회하므로 `ospfCosts`가 **4개**가 된다. 그러나 `api_server.py:134`의 스키마 주석은
`# 6개: r1-r2,r1-r3,r2-r3,r2-r4,r3-r4,r1-r4`이고, `_obs_from_payload()`(190행)는 세 리스트를
그대로 이어붙인다 → **4+4+4 = 12차원**. 두 에이전트 모두 14차원을 기대한다
(`few_shot_agent.py:24 OBS_DIM = 2*N_NODES + N_LINKS`) → 행렬 곱 오류.
게다가 실제 OSPF cost를 조회하지 않고 항상 10을 보내므로 값 자체도 틀렸다.

**(b) 정규화 규약 충돌 → 값의 의미가 반대**

| 계층 | latency 처리 | 의미 |
| --- | --- | --- |
| `MetricNormalizer.java:25` | `1 - lat/500` | **클수록 좋음** (0.98 = 정상) |
| `NetworkEnv._obs_from_metrics` | `lat/200` | **클수록 나쁨** (0.98 = 심각) |
| `api_server._obs_from_payload` | 정규화 없음 (원시 ms) | — |

collector가 Kafka에 **정규화된** 값을 발행하므로(`NetworkMetricCollector:47`), 에이전트는
"지연이 심각하다"를 "지연이 정상이다"로 읽는다. 스케일(500 vs 200)도 다르다.

**(c) `/anomaly` 판정이 영구히 발화하지 않음**

`OrchestrationService.java:51`은 **정규화된** 메트릭으로 `aiEngine.isAnomaly()`를 호출한다.
서버의 `analyze_anomaly()`는 `SLA_LATENCY_MS = 50.0` (밀리초 원시값)과 비교한다.
정규화된 지연은 0~1 범위이므로 `latency > 50`은 **절대 참이 되지 않는다.**

### 수정 방침

**"수집 계층은 원시값만 발행하고, AI 입력 정규화는 AI 엔진이 책임진다"** 로 규약을 통일한다.
AI 엔진은 이미 `_metrics_to_obs()`에서 올바르게 정규화하고 있으므로, 그 규약에 나머지를 맞춘다.

### 단계

**T4-1. collector가 원시 메트릭을 발행하도록 변경**

`NetworkMetricCollector.collectNetworkMetrics()`에서 `normalizer.normalize(raw)` 호출을 제거하고
`publisher.publish(raw)`로 바꾼다. `MetricNormalizer`는 삭제하지 말고, 클래스 주석에
"AI 엔진 입력 규약과 불일치하여 파이프라인에서 제외됨 (NetworkEnv: bw/1000, lat/200, loss 원시)"를
명시한 뒤 미사용으로 남기거나, 규약을 `NetworkEnv`에 맞춰 고쳐 `AiEngineClient` 직전에서만 쓴다.
**둘 중 하나를 선택하고 이유를 커밋 메시지에 적을 것.**

**T4-2. orchestrator가 실제 OSPF cost 6개를 조회**

`MininetClient`에 GET 메서드를 추가한다(현재는 `setOspfCost` PUT만 있다).

```java
/** GET /ospf/costs → {"r1-r2": 10, ...} */
public Map<String, Integer> fetchOspfCosts()
```

`AiEngineClient.decideAction()`이 이를 받아 **링크 순서를 고정해** 6개를 보낸다.
순서는 `api_server.py:193 _metrics_to_obs`의 `LINK_ORDER`와 **반드시** 동일해야 한다:

```
["r1-r2", "r1-r3", "r2-r3", "r2-r4", "r3-r4", "r1-r4"]
```

조회 실패 시에는 행동을 내리지 말고 스킵한다(현재처럼 10으로 채우면 틀린 관측으로 조치하게 된다).

**T4-3. 노드 순서 고정**

`decideAction()`은 `metricBuffer` **도착 순서**로 배열을 만든다. Kafka 파티션 키가 nodeId라
순서 보장이 없다. `["r1","r2","r3","r4"]` 순으로 정렬하고, 4개가 다 모이지 않았으면 스킵한다.
`OrchestrationService`의 버퍼도 노드별 최신값 맵(`Map<String, NetworkMetricDto>`)으로 바꾸는 편이
안전하다 — 현재는 같은 노드가 두 번 오면 라운드가 어긋난다.

**T4-4. 서버 측 정규화 일치**

`api_server._obs_from_payload()`를 `_metrics_to_obs()`와 동일한 규약으로 고친다.

```python
def _obs_from_payload(bws, lats, costs) -> np.ndarray:
    arr = [b / MAX_BW for b in bws] + [l / MAX_LAT for l in lats] + [c / MAX_COST for c in costs]
    return np.clip(np.array(arr, dtype=np.float32), 0.0, 1.0)
```

추가로 `StatePayload`에 길이 검증을 넣어, 잘못된 차원이 오면 조용한 오작동 대신
**HTTP 400과 명확한 메시지**를 반환하게 한다 (4/4/6이 아니면 거부). 이번 버그가 몇 달 동안
발견되지 않은 이유가 바로 이 검증의 부재다.

**T4-5. `/anomaly` 확인**

T4-1로 원시값이 흐르게 되면 `analyze_anomaly()`의 SLA 규칙이 정상 동작한다. 코드 수정은 불필요하고
**검증만** 한다.

### 검증

```bash
# 1) 서버 2개 기동 후, /action 계약을 직접 호출
curl -X POST http://127.0.0.1:8000/action -H "Content-Type: application/json" -d '{
  "bandwidths":[500,500,500,500],
  "latencies":[10,10,10,10],
  "ospfCosts":[10,10,10,10,10,10],
  "useFewShot":true }'
# → 200 OK, {"targetLink":..., "newOspfCost":..., "agentType":"maml"}

# 2) 잘못된 차원은 400으로 거부되는지
curl -X POST http://127.0.0.1:8000/action -H "Content-Type: application/json" -d '{
  "bandwidths":[500,500,500,500],"latencies":[10,10,10,10],
  "ospfCosts":[10,10,10,10],"useFewShot":true }'
# → 400

# 3) SLA 위반 값으로 /anomaly가 실제로 발화하는지
curl -X POST http://127.0.0.1:8000/anomaly -H "Content-Type: application/json" \
  -d '{"nodeId":"r1","bandwidth":50,"latency":120,"packetLoss":0.03}'
# → {"isAnomaly": true, ...}

# 4) Java 전체 경로 (Kafka 필요)
docker compose up -d zookeeper kafka
cd collector-service && ./mvnw spring-boot:run      # 별도 터미널
cd orchestrator-service && ./mvnw spring-boot:run   # 별도 터미널
curl -X POST http://127.0.0.1:5001/debug/congestion/r3-r4
# orchestrator 로그에 "Action decided: link=... cost=..." 와
# "Orchestration applied" 가 예외 없이 찍히는지 확인
```

### 완료 기준

- [ ] 정상 페이로드로 `/action`이 200을 반환 (12차원 오류 소멸)
- [ ] 잘못된 차원이 400으로 거부됨
- [ ] Kafka에 흐르는 메트릭이 원시값이고, `/anomaly`가 SLA 위반에서 true를 반환
- [ ] orchestrator 로그에서 혼잡 주입 → 행동 결정 → OSPF 적용이 예외 없이 이어짐
- [ ] `PROJECT_OVERVIEW.md` §7 "코드에서 확인된 이슈"의 해당 항목을 수정 결과로 갱신

### 대안 (범위를 줄이고 싶을 때)

`/action` 계약을 고치는 대신, orchestrator가 **`/auto-step`을 호출**하도록 바꾸면 검증된 Python
루프를 그대로 재사용하게 되어 (a)(b)(c)가 한 번에 사라진다. 다만 Java 계층은 "폐쇄 루프 트리거"로
역할이 축소된다. **이 대안을 택할 경우 README의 아키텍처 설명도 함께 고칠 것.**

---

## T5. `sample_efficiency.json` 측정 조건 명시 (P2)

### 현상

`results/sample_efficiency.json`의 MAML은 200 iter(96,000 샘플)에서 TTR **111.4**다.
그런데 README는 "MAML이 ~100 에피소드 내 적응"이라고 서술한다. 모순처럼 보이지만 실제로는
**측정 경로가 다르다**: 이 JSON은 `run_experiment.py`의 오프라인 경로(Analytics override 없음)이고,
3.78이라는 수치는 `/auto-step`(override 포함) 경로다.

조건 표기가 없어서 두 수치가 같은 축에서 비교되는 것처럼 읽힌다.

### 수정

1. `run_experiment.py`의 `sample_efficiency_experiment()` 결과 저장부에 조건 메타데이터를 함께 쓴다:
   ```python
   {"ppo": [...], "maml": [...],
    "_condition": "offline NetworkEnv (local_mode), Analytics override 없음, "
                  "TEST 링크 평가, max_steps=200 → 미해결 시 TTR=200"}
   ```
2. 기존 `sample_efficiency.json`에는 **측정값은 그대로 두고** `_condition` 키만 추가한다(§0.2-2 예외).
3. README에서 이 수치를 인용하는 문장에 각주를 단다:
   > 이 곡선은 Analytics override가 없는 오프라인 평가 경로의 결과로, `/auto-step` 폐쇄 루프
   > 수치(3.78)와 직접 비교할 수 없다.
4. "500배 샘플 효율" 같은 문구가 어느 측정에 근거하는지 확인하고, 근거가 없으면 삭제하거나
   근거 있는 표현으로 바꾼다.

### 완료 기준

- [ ] JSON에 `_condition` 존재, 측정값 불변
- [ ] README의 샘플 효율 서술에 경로 차이가 명시됨

---

## T6. 미사용 요소 정리 (P2)

| 대상 | 현상 | 조치 |
| --- | --- | --- |
| `ai-engine/requirements.txt` | `learn2learn==0.2.0`이 설치 목록에 있으나 import되지 않음 (MAML은 `torch.autograd.grad`로 직접 구현) | 해당 줄 삭제. `few_shot_agent.py` 상단 docstring에 "learn2learn 없이 순수 PyTorch 구현"이 이미 적혀 있으므로 근거 충분 |
| `simulation/topology.py` | Mininet 토폴로지 정의는 있으나 현재 파이프라인이 사용하지 않음 (`mock_snmp_agent`는 `metric_generator`를 씀) | 삭제하지 말 것. docstring 첫 줄에 "현재 실험 파이프라인은 이 파일을 사용하지 않는다 — 실장비/Mininet 전환 시의 참조 구현" 한 줄 추가 |
| `docker-compose.yml` postgres / redis | 서비스는 뜨지만 영속화 코드 없음 (`application.yml`에 datasource만 설정) | 두 서비스 위에 `# 현재 미사용 — 향후 메트릭 이력/캐시용 예약` 주석 추가. 제거는 하지 말 것(설정이 `application.yml`에 남아 있어 제거하면 Spring 부팅이 깨진다) |

**주의**: `learn2learn` 제거 후 `pip install -r requirements.txt`가 깨지지 않는지,
`python -c "import ai_engine..."` 대신 `python ai-engine/api_server.py`가 정상 기동하는지 확인한다.

### 완료 기준

- [ ] `api_server.py`, `few_shot_agent.py --train` 모두 정상 동작
- [ ] 세 파일에 주석/문서 반영

---

## T7. `dashboard.html`이 정적 데모임을 명시 (P2)

### 현상

`dashboard.html`(= 루트 `index.html`, 바이트 동일)에는 **`fetch` 호출이 하나도 없다.**
메트릭은 `_genMetrics()`가 JS에서 난수로 생성하고, 실험 결과는 `EMBEDDED_SUMMARY` /
`EMBEDDED_EFFICIENCY` 상수로 하드코딩되어 있다. GitHub Pages 데모로는 타당하지만, 화면만 보면
실행 중인 시스템을 모니터링하는 것처럼 보인다.

### 수정 — 최소안 (권장)

페이지 상단(제목 옆)에 명확한 배지를 넣는다.

```html
<span class="badge-demo">정적 데모 — 실행 중인 시스템의 데이터가 아닙니다
  (지표는 브라우저에서 생성, 실험 결과는 2026-04-22 측정치 임베드)</span>
```

`EMBEDDED_SUMMARY` 선언부 위에도 같은 취지의 주석을 단다. **두 파일 모두 수정할 것**
(`dashboard.html`과 루트 `index.html`은 별개 파일이며 현재 내용이 동일하다).

### 선택안

`mock_snmp_agent.py`에 이미 `/ai/<path>` 프록시가 있으므로, "라이브 모드" 토글을 넣어
서버가 살아 있으면 `:5001/metrics`와 `:5001/ai/auto-step`을 폴링하고, 실패하면 임베드 데모로
폴백하게 만들 수 있다. **최소안을 먼저 커밋한 뒤 별도 커밋으로 진행할 것.**

### 완료 기준

- [ ] 두 파일 모두에 데모 표시가 보이고, 임베드 데이터의 측정 일자가 명시됨

---

## T8. 최종 문서 동기화 (P3, 마지막에 수행)

앞의 태스크가 끝난 뒤 문서를 실제 상태에 맞춘다.

1. **`cowork/PROJECT_OVERVIEW.md` §7** — 해결된 항목은 "해결됨(날짜, 커밋)"으로 갱신하고
   **삭제하지 않는다.** 미해결 항목은 그대로 둔다.
2. **`README.md`**
   - "성능 검증": T1 결과(기존/신규 병기), T2의 학습된 PPO 행 추가
   - "Ablation Study": T3 수치로 교체 + 출처 명시
   - "한계 및 향후 연구": 해결된 항목은 취소선 처리(기존에 OSPF 인증 항목을 그렇게 처리한 전례가 있다)
   - "개발 일지": 이번 작업분을 날짜 항목으로 추가. **잘 안 된 시도도 적을 것.**
3. **`experiments/experiment_report.md`** — §7.3 ablation 표를 T3 결과로 교체
4. `experiments/CICDDOS2019_SETUP.md` — T1으로 로더 동작이 바뀌었으면 "알려진 한계" 절 갱신

### 완료 기준

- [ ] README / experiment_report / PROJECT_OVERVIEW / results JSON 사이에 **수치 모순이 없음**
- [ ] 각 수치에 측정 조건(경로, n, 날짜)이 붙어 있음
- [ ] 개선되지 않은 것을 개선된 것처럼 쓴 문장이 없음

---

## 부록 A. 태스크별 커밋 메시지 예시

```
fix(experiments): aggregate pkt_rate from Flow Packets/s instead of flow-start windowing
fix(orchestrator): send 6 ordered OSPF costs and raw metrics to /action
feat(experiments): add trained PPO baseline for fair MAML comparison
docs: sync ablation numbers with 50-episode result file
chore: drop unused learn2learn dependency
```

## 부록 B. 손대면 안 되는 것

| 대상 | 이유 |
| --- | --- |
| `SecurityAnomalyDetector`의 6피처 시그니처 | 시뮬레이션 경로(`metric_generator`, `SecurityMetricPayload`)와 연동. 바꾸려면 3개 파일 동시 수정 필요 |
| `NetworkEnv`의 14차원 관측 / 30 행동 | 저장된 체크포인트(`maml_network.pt`, `ppo_network.zip`)와 호환이 깨진다 |
| FastAPI 엔드포인트 경로·응답 스키마 | Java 서비스와 대시보드가 의존 |
| `metric_generator`의 `BYPASS_COST_THRESHOLD = 100` | 모든 기존 실험 결과의 전제. 바꾸면 과거 수치와 비교 불가 |
| `data/cicddos2019/Syn.csv` | 1.87 GB, `.gitignore` 대상. 커밋 금지, 이동/삭제 금지 |
| 기존 `results/*.json`의 측정값 | 재실행으로만 갱신. 새 결과는 새 파일명으로 |
