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

## 알려진 한계

- `bandwidth`/`latency`/`packet_loss`는 CICDDoS2019에 대응 컬럼이 없어 고정값(placeholder)을
  사용합니다 — 실제로 검증되는 건 `syn_ratio`/`unique_src_count`/`pkt_rate` 3개 차원뿐입니다.
- CICDDoS2019에는 포트스캔 공격이 없습니다. `attack_type == "portscan"` 분기는 이
  데이터셋으로 검증할 수 없습니다 (실패가 아니라 데이터셋 특성입니다).
- 공격일 CSV는 대부분(95%+) 공격 트래픽입니다. `--benign-warmup` 값을 0과 200 등으로
  바꿔가며 비교해보면 cold-start 학습 민감도를 확인할 수 있습니다.
