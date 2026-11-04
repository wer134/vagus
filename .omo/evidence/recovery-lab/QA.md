# 대화형 복구 실험실 검증

## 구현 경로

기존 `api_server.py`와 독립 `recovery_lab_server.py`가 같은 APIRouter를 사용한다. `/recovery`의 실제 화면은 `/recovery/options`, `/recovery/sessions`, 세션 GET/DELETE, `/step`을 호출한다. SessionStore는 공통 `Simulator`와 정책 factory를 사용하며 controller에는 Observation과 현재 tick만 전달한다. 관측과 evaluator truth는 응답과 화면에서 구분한다.

## 기능 검증

- API self-test: 9개 정책 모두 같은 seed·프리셋의 배치 평가와 명령 수, 완료 시점, 완료 오판, 위험한 복귀, 누적 과부하가 일치.
- 404 미존재 세션, 422 미등록 정책/잘못된 범위/NaN/0 tick, 409 종료 후 진행, 세션 독립과 삭제, asset 하위경로 제한 검증.
- 기존 AI 엔진의 실제 FastAPI TestClient OpenAPI 응답에 새 경로와 기존 `/auto-step`이 함께 존재함을 확인.
- 전체 selftest: 18개 통과, 0개 실패, 2개 생략(정책 체크포인트·Maven).
- 신규 Python Ruff 기본 오류 검사 및 JavaScript 문법 검사 통과.
- 기존 HTML 두 사본의 byte identity 유지. 역사적 고정 평가의 소스·결과는 수정하지 않음.

## 실제 화면 검증

사용자 브라우저 제어 호스트는 URL 검증 제한으로 사용할 수 없었다. 사용자 창에는 입력하지 않고, 새 임시 프로필의 실제 Chrome 실행 파일을 Playwright로 제어해 로컬 주소만 테스트했다.

`scripts/test_recovery_lab_browser.py`로 375/768/1280px 초기·완료 화면과 workspace, 진행·오류 화면을 캡처했다. 페이지 JavaScript 오류와 본문 가로 넘침이 없음을 확인했다. 1 tick, 자동 진행/일시 정지, 끝까지 실행, 정책 변경·새 실험, checkbox, 전체 tick 기록 전환, JSON 다운로드가 통과했다. 503 오류 표시는 테스트 전용 응답으로 검증했다.

토큰·DOM/SVG·한글·반응형·오류 상태에 대한 독립 시각 검토 PASS, 공통 정책 연결·세션 격리·입력 경계·자동 진행 race에 대한 독립 코드/시각 검토 PASS. 두 검토 모두 blocking 없음. Lighthouse 점수는 측정하거나 주장하지 않았다.

## 재검증

`ai-engine`에서 `py -X utf8 -B -m recovery_lab.test_api`.

실험실 서버를 실행한 뒤, `autonomous-network-mgmt`에서 `PYTHONUTF8=1 uv run --no-project --with playwright scripts/test_recovery_lab_browser.py`를 실행한다. 브라우저 검증은 Chrome 설치가 필요하며, 실행 환경에 맞는 `--python`을 지정할 수 있다. 캡처와 다운로드 fixture는 이 경로에 재생성되며 Git에서 제외한다.
