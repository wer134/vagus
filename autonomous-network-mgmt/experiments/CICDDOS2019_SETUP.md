# CICDDoS2019 데이터셋 준비 가이드

`validate_security_detector.py`로 `SecurityAnomalyDetector`(DDoS 탐지)를 실데이터로
검증하려면 CICDDoS2019 데이터셋이 필요합니다. UNB(뉴브런즈윅 대학)가 가입/이용약관
동의를 요구하기 때문에 다운로드는 자동화할 수 없고 아래 절차를 직접 수행해야 합니다.

## 1. 다운로드

1. https://www.unb.ca/cic/datasets/ddos-2019.html 접속
2. 다운로드 링크를 따라가 가입/이용약관 동의 후 데이터셋 접근 권한을 받습니다.
3. 데이터셋은 날짜별로 나뉘어 있습니다:
   - `01-12` (학습일): `DrDoS_DNS`, `DrDoS_LDAP`, `DrDoS_MSSQL`, `DrDoS_NetBIOS`, `DrDoS_NTP`, `DrDoS_SNMP`, `DrDoS_SSDP`, `DrDoS_UDP`, `Syn`, `TFTP`, `UDPLag`
   - `03-11` (테스트일): `LDAP`, `MSSQL`, `NetBIOS`, `Syn`, `UDP`, `UDPLag`, `Portmap`

## 2. 권장 파일

**`03-11/Syn.csv`** 부터 시작하세요.

- SYN flood 공격이라 기존 `SecurityAnomalyDetector`의 `syn_ratio` 임계치(≥0.30) 로직과
  직접 대응되어 가장 명확한 검증이 가능합니다.
- 다른 날짜의 통합 CSV(`DrDoS_UDP.csv` 등)보다 파일 크기가 작아 다루기 쉽습니다.

전체 날짜를 한꺼번에 받을 필요는 없습니다. 단일 공격 파일로도 충분히 검증 가능합니다.

## 3. 파일 배치

다운로드한 CSV를 아래 경로에 저장하세요:

```
autonomous-network-mgmt/data/cicddos2019/Syn.csv
```

`data/` 디렉터리는 `.gitignore`에 등록되어 있어 커밋되지 않습니다 (용량이 크고
재배포 라이선스 문제가 있으므로 git에 포함하지 마세요).

## 4. 컬럼명 확인 (선택)

CICDDoS2019 배포 버전에 따라 컬럼명에 공백이 들어가 있거나(`" Timestamp"`) 대소문자가
다를 수 있습니다. `cicddos_loader.py`가 이를 자동으로 정규화하지만, 혹시 로딩 시
`ValueError: ... 필요한 컬럼이 없습니다` 에러가 나면 CSV 헤더를 열어 다음 컬럼이
(이름이 다르더라도) 존재하는지 확인하세요:

- `Timestamp`
- `Source IP`
- `SYN Flag Count`
- `Total Fwd Packets`
- `Total Backward Packets`
- `Label`

## 5. 실행

```bash
cd autonomous-network-mgmt/experiments
python validate_security_detector.py --csv-path ../data/cicddos2019/Syn.csv --benign-warmup 200
```

결과는 `experiments/results/cicddos_validation.json`에 저장되고, 콘솔에 precision/recall/F1
요약 표가 출력됩니다.

## pkt_rate 집계 방식 (2026-09 재설계)

로더(`cicddos_loader.py`)는 CSV에 `Flow Duration` / `Flow Packets/s` 컬럼이 있으면
**flow_rate_sum** 모드로 동작합니다: pkt_rate = 윈도우 내 플로우 전송률(`Flow Packets/s`)의 합.

- 기존 방식(플로우 시작 윈도우에 총 패킷수 귀속)은 87µs짜리 공격 플로우(실전송률 ~40k pps)를
  수백 pps로 축소시켜 recall 0.12의 주 원인이었습니다.
- `Flow Packets/s`가 inf/NaN이면 duration으로 재계산하고, 불가하면 제외 후
  결과 JSON의 `loader_stats`에 건수를 기록합니다.
- 두 컬럼이 없는 배포본에서는 기존 방식(flow_start_window)으로 폴백하며
  경고를 출력합니다. 어느 모드였는지는 결과 JSON의 `aggregation_mode`로 확인하세요.

## 알려진 한계

- `bandwidth`/`latency`/`packet_loss`는 CICDDoS2019에 대응 컬럼이 없어 고정값(placeholder)을
  사용합니다 — 실제로 검증되는 건 `syn_ratio`/`unique_src_count`/`pkt_rate` 3개 차원뿐입니다.
- 이 배포본의 `SYN Flag Count`는 공격 플로우에서도 거의 항상 0입니다(비영 비율 ~0.02%,
  결과 JSON `loader_stats.attack_flows_syn_nonzero`로 실측 확인 가능) — `syn_ratio`
  임계치(0.30)는 발화하지 않으며, 탐지는 사실상 `pkt_rate`/`unique_src_count`에 의존합니다.
- flow_rate_sum 모드의 pkt_rate는 "윈도우와 겹치는 플로우 전송률의 합"으로, 물리적 순간
  pps의 상한 근사입니다 (탐지 피처로서의 분리력이 목적이며 정확한 pps 측정이 아닙니다).
- CICDDoS2019에는 포트스캔 공격이 없습니다. `attack_type == "portscan"` 분기는 이
  데이터셋으로 검증할 수 없습니다 (실패가 아니라 데이터셋 특성입니다).
- 공격일 CSV는 대부분(95%+) 공격 트래픽입니다. `--benign-warmup` 값을 0과 200 등으로
  바꿔가며 비교해보면 cold-start 학습 민감도를 확인할 수 있습니다. 공격 base rate가
  높으므로 "전부 공격 예측"의 F1(결과 JSON `all_attack_baseline_f1`)을 넘는지 반드시
  함께 확인하세요.
