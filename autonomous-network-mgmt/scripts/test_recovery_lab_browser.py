from pathlib import Path
import json
import os

from playwright.sync_api import sync_playwright


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    captures = root / ".omo" / "evidence" / "recovery-lab"
    captures.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(chrome), headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://127.0.0.1:8000/recovery")
        page.get_by_role("button", name="1 tick 진행", exact=True).wait_for(state="visible")
        page.wait_for_function("!document.getElementById('step').disabled")
        assert page.locator("#policy option").count() == 9
        assert page.locator("#regime option").count() == 6
        for width in (375, 768, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            page.screenshot(path=str(captures / f"ready-{width}.png"), full_page=True)
        page.get_by_role("button", name="1 tick 진행", exact=True).click()
        page.wait_for_function("document.getElementById('tick').textContent.startsWith('1 /')")
        page.get_by_role("button", name="자동 진행", exact=True).click()
        page.wait_for_function("parseInt(document.getElementById('tick').textContent) >= 8")
        page.get_by_role("button", name="일시 정지", exact=True).click()
        page.wait_for_function("!document.getElementById('step').disabled")
        page.screenshot(path=str(captures / "running-1280.png"), full_page=True)
        page.get_by_role("button", name="끝까지 실행", exact=True).click()
        page.wait_for_function("document.getElementById('tick').textContent.startsWith('120 /')")
        assert page.locator("#commands").inner_text() == "2"
        assert "관측 구간 내 안전" in page.locator("#safety").inner_text()
        assert page.locator("#step").is_disabled()
        with page.expect_download() as pending:
            page.get_by_role("button", name="JSON 저장", exact=True).click()
        download = pending.value
        download.save_as(str(captures / "interactive.json"))
        payload = json.loads((captures / "interactive.json").read_text(encoding="utf-8"))
        assert payload["exploratory"] and payload["tick"] == 120
        assert not payload["unsafe_restore"] and payload["declared_tick"] is not None
        for width in (375, 768, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            page.screenshot(path=str(captures / f"finished-{width}.png"), full_page=True)
            page.locator(".toolbar").scroll_into_view_if_needed()
            page.screenshot(path=str(captures / f"workspace-{width}.png"))
        page.locator("#changes-only").uncheck()
        assert page.locator("#events tr").count() == 120
        page.locator("#changes-only").check()
        assert page.locator("#events tr").count() < 30
        page.locator("#policy").select_option("forecast")
        page.get_by_role("button", name="새 실험 시작", exact=True).click()
        page.wait_for_function("document.getElementById('tick').textContent.startsWith('0 /') && !document.getElementById('step').disabled")
        page.get_by_role("button", name="끝까지 실행", exact=True).click()
        page.wait_for_function("document.getElementById('tick').textContent.startsWith('120 /')")
        assert "위험한 복귀 발생" in page.locator("#safety").inner_text()
        page.locator("#background_via_source").check()
        page.locator("#initial_missing").check()
        page.get_by_role("button", name="새 실험 시작", exact=True).click()
        page.wait_for_function("document.getElementById('tick').textContent.startsWith('0 /') && !document.getElementById('step').disabled")
        page.route("**/recovery/sessions/*/step", lambda route: route.fulfill(status=503, json={"detail": "테스트용 연결 오류"}))
        page.get_by_role("button", name="1 tick 진행", exact=True).click()
        page.locator("#error").wait_for(state="visible")
        assert "테스트용 연결 오류" in page.locator("#error").inner_text()
        page.screenshot(path=str(captures / "error-1280.png"), full_page=True)
        page.unroute("**/recovery/sessions/*/step")
        assert not errors, errors
        browser.close()
    print(f"격리 브라우저 기능·반응형·다운로드·오류 표시 검증 통과: {captures}")


if __name__ == "__main__":
    main()
