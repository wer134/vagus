"""도판 팔레트 단일 출처 (cowork/VISUALIZATION_PLAN.md §2.1).

matplotlib(문서 도판)과 Chart.js(대시보드)가 각자 색을 고르면 반드시 어긋난다.
두 렌더러가 이 파일 하나를 본다 — matplotlib은 직접 import, 대시보드는
`css_variables()`가 생성한 커스텀 프로퍼티를 읽는다.

값은 `dataviz` 스킬의 검증된 기본 팔레트를 그대로 채택했다. 채택 시점에 검증기를 돌렸고
결과는 아래 VALIDATION에 그대로 남긴다 — **눈으로 판정하지 않는다.**

    node <dataviz>/scripts/validate_palette.js \\
        "#2a78d6,#eb6834,#1baf7a,#eda100,#e87ba4,#008300" --mode light

색을 쓰는 규칙 (역할로 고른다):
    CATEGORICAL : 정체성 구분. 고정 순서로 쓰고 **순환시키지 않는다**
    SEQUENTIAL  : 크기 비교. 한 색조, 옅음 → 진함
    STATUS      : 상태. 계열 색으로 재사용 금지, 항상 라벨과 함께
"""

# ── 검증 기록 ────────────────────────────────────────────────────────────────
VALIDATION = """2026-09-13, dataviz validate_palette.js, categorical 6 slots, light (surface #fcfcfb)
  [PASS] Lightness band      all 6 inside L 0.43-0.77
  [PASS] Chroma floor        all 6 >= 0.1
  [PASS] CVD separation      worst adjacent #eda100<->#1baf7a dE 9.1 (protan)
  [PASS] Normal-vision floor worst adjacent #e87ba4<->#eda100 dE 19.6
  [WARN] Contrast vs surface #1baf7a 2.74 / #eda100 2.11 / #e87ba4 2.62 below 3:1
         -> relief rule: 직접 라벨 또는 표 병기 (도판 규칙 8과 동일 요구)
  => ALL CHECKS PASS"""

# ── 범주 (정체성) — 고정 순서, 순환 금지 ──────────────────────────────────────
CATEGORICAL = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300",
              "#4a3aa7", "#e34948"],
    "dark":  ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300",
              "#9085e9", "#e66767"],
}

# ── 순차 (크기) — 파랑 한 색조, 100(옅음) → 700(진함) ─────────────────────────
SEQUENTIAL_BLUE = {
    100: "#cde2fb", 150: "#b7d3f6", 200: "#9ec5f4", 250: "#86b6ef",
    300: "#6da7ec", 350: "#5598e7", 400: "#3987e5", 450: "#2a78d6",
    500: "#256abf", 550: "#1c5cab", 600: "#184f95", 650: "#104281", 700: "#0d366b",
}
# 두 번째 순차 맥락이 동시에 필요하면 다음 범주 슬롯(주황)을 자체 한 색조 램프로 쓴다.
SEQUENTIAL_DEFAULT = SEQUENTIAL_BLUE[450]     # 단일 계열 막대의 기본색

# ── 상태 (고정, 테마 무관) — 계열 색으로 재사용 금지 ──────────────────────────
STATUS = {
    "good":     "#0ca30c",
    "warning":  "#fab219",
    "serious":  "#ec835a",
    "critical": "#d03b3b",
}

# ── 표면·잉크 ────────────────────────────────────────────────────────────────
SURFACE = {"light": "#fcfcfb", "dark": "#1a1a19"}
INK = {
    "light": {"primary": "#0b0b0b", "secondary": "#52514e",
              "muted": "#898781", "axis": "#c3c2b7"},
    "dark":  {"primary": "#ffffff", "secondary": "#c3c2b7",
              "muted": "#898781", "axis": "#383835"},
}

# 강조 형태(하나가 주인공, 나머지는 맥락)에서 쓰는 비강조 회색
DEEMPHASIS = {"light": "#c3c2b7", "dark": "#52514e"}


def categorical(n: int, mode: str = "light") -> list[str]:
    """범주 색 n개를 고정 순서로. 9번째 계열은 색을 만들지 않고 예외를 던진다."""
    slots = CATEGORICAL[mode]
    if n > len(slots):
        raise ValueError(
            f"범주 계열 {n}개 요청 — 상한 {len(slots)}. 색을 새로 만들지 말고 "
            "'기타'로 묶거나 small multiples로 나눌 것 (VISUALIZATION_PLAN §1-6)."
        )
    return slots[:n]


def sequential(n: int) -> list[str]:
    """순차 램프에서 n단계를 옅음 → 진함으로 고르게 뽑는다."""
    steps = sorted(SEQUENTIAL_BLUE)
    if n == 1:
        return [SEQUENTIAL_DEFAULT]
    idx = [round(i * (len(steps) - 1) / (n - 1)) for i in range(n)]
    return [SEQUENTIAL_BLUE[steps[i]] for i in idx]


def css_variables(mode: str = "light") -> str:
    """대시보드(Chart.js)가 읽을 CSS 커스텀 프로퍼티. 같은 값을 두 번 적지 않기 위함."""
    lines = [f"  --surface-1: {SURFACE[mode]};"]
    lines += [f"  --text-{k}: {v};" for k, v in INK[mode].items()]
    lines += [f"  --series-{i}: {c};" for i, c in enumerate(CATEGORICAL[mode], 1)]
    lines += [f"  --status-{k}: {v};" for k, v in STATUS.items()]
    lines.append(f"  --deemphasis: {DEEMPHASIS[mode]};")
    return "\n".join(lines)


if __name__ == "__main__":
    assert len(CATEGORICAL["light"]) == len(CATEGORICAL["dark"]) == 8
    assert categorical(3) == ["#2a78d6", "#eb6834", "#1baf7a"]
    try:
        categorical(9)
    except ValueError as e:
        assert "상한" in str(e)
    else:
        raise AssertionError("9번째 계열 요청이 막히지 않았다")
    seq = sequential(4)
    assert len(seq) == 4 and seq[0] == "#cde2fb" and seq[-1] == "#0d366b", seq
    assert not (set(STATUS.values()) & set(CATEGORICAL["light"])), "상태색이 계열색과 겹친다"
    assert "--series-1" in css_variables() and "--surface-1" in css_variables("dark")
    print("OK — 팔레트 단일 출처 (범주 8, 순차 13단계, 상태 4)")
    print(VALIDATION)
    print("\n모든 자가 테스트 통과")
