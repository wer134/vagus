# 합성 복구 제어 시뮬레이터

제공된 인터페이스 명세를 바탕으로 새로 재구성한 연구용 모델이다. 기존 simulator, PPO/MAML 모델 또는 논문의 실험을 재현한 구현이 아니다.

`types.py`는 불변 관측과 명령, `scenario.py`는 외생 일정, `routing.py`는 라우팅, `simulation.py`는 시간 전진, `truth.py`는 평가기 전용 상태를 담당한다. 제어기는 `Observation`과 현재 tick만 입력받으며 `Scenario`, `Simulator`, `TruthSnapshot`을 받지 않는다.

## 물리적 계약

- 링크 순서는 `a-b, a-c, b-d, c-d, b-c`이고 기본 비용은 모두 10이다. 각 링크의 용량 `60,220,60,220,80`은 양방향 전송량 합계에 적용한다.
- target는 `a→d`, background는 `b→c`이며 정상 수요는 35, 15이다. 장애 구간 target 수요는 180이다. 초기 tick 0/1을 제외하면 각 수요에 seed로 고정된 ±3% 변화가 있다.
- 양수 비용을 가진 단순 최단 경로들을 찾고, 비용이 같은 경로 사이에 commodity를 균등 분배한다. 이는 경로별 ECMP 추상화이며 실제 라우터의 hop별 ECMP나 packet hash를 구현하지 않는다.
- 기본 경로의 장애 수요는 `a-b-d/a-c-d`에 90씩 나뉘어 용량 60 경로를 과부하시킨다. `CostCommand("a-b",100)`은 target를 용량 220의 `a-c-d`로 우회한다. 복귀는 비용 10 명령이다.
- `background_via_source=True`는 알려진 waypoint 정책으로 배경 흐름을 실제 `b-a-c`로 보낸다. 이때 source 인접 링크의 부하 합은 `target + 2×background`가 되므로 source-cut 보정의 가정을 위반한다.

## 시간과 관측 계약

`Simulator(scenario).advance()`는 정확히 한 tick 전진한다. 해당 tick에 명령 적용, 경로 수렴, 샘플링 순으로 처리한 뒤 도착한 새 관측들을 반환한다. 같은 도착 tick에서는 sample tick 순서로 정렬한다. `issue(command)`는 접수 ACK를 즉시 반환하며 비용 변경은 `issue_tick + max(1, propagation_delay)`에 적용된다.

비용이 변경된 tick부터 `convergence_ticks` 동안 이전/새 경로의 **비율**을 convex combination으로 혼합한다. 매 tick 현재 수요를 곱하므로 수요가 바뀌거나 수렴 도중 새 명령이 적용되어도 commodity별 흐름이 보존된다. 같은 목표를 반복 요청하면 진행 중인 수렴을 초기화하지 않는다.

관측은 `sampled_at`, `arrived_at`, 측정 부하, 그때의 설정 비용, 용량, 편향된 보고 수요, 알려진 background waypoint 여부만 포함한다. 첫 기준 샘플은 `initial_missing=True`가 아니면 tick 1에 바로 제공한다. 이후에는 지연, 역순 도착, 샘플 전체 누락, 부하 곱셈 잡음을 지원한다. 부분 링크 누락은 모델 범위 밖이다.

모든 외생 변화는 `(seed,tick,field)` 키의 해시로 정해진다. 정책의 명령 수나 난수 소비가 수요, 잡음, 누락, 지연 일정을 바꿀 수 없다. 기본 장애 시작·끝은 8/30이며 평가 bank가 seed별 시작·끝을 명시해 고정한다.

`TruthSnapshot.loads_safe`는 실제 최대 이용률 ≤ 1, `settled`는 대기 명령과 잔류 경로가 없음, `safe`는 이 두 조건의 결합이다. `restored`는 기본 비용과 `settled`의 결합이므로 안전성은 `loads_safe`와 별도로 확인해야 한다. `default_safe`는 현재 실제 수요를 기본 비용 경로로 보낼 때의 반사실적 안전성이다. 이 상태와 실제 수요는 평가기만 사용한다.

## 검증

작업 디렉터리 `autonomous-network-mgmt/experiments`:

```powershell
python -m recovery_control.test_simulation
```

외부 패키지 없이 assertion self-test를 실행한다. 흐름 보존, ECMP/우회, ACK와 적용 지연, 수렴 중 재명령·수요 변화, 복귀 판정, 관측 시점 보존, paired 외생 일정, 역순·누락, source 경유와 잘못된 입력 거부를 검증한다.
