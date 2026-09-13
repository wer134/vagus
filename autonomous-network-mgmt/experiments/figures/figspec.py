"""도판 공통 규약 — 측정 조건 스탬프와 저장 (cowork/VISUALIZATION_PLAN.md §1-2, §2.3).

두 가지를 강제한다.

1. **조건 없는 도판을 만들 수 없다.** `save()`는 결과 문서의 `git_commit`/`seed`/`contract`를
   읽어 그림 아래에 찍는다. 조건이 없는 그림은 AUDIT P1과 같은 결함 — 나중에 어느 자로 잰
   건지 알 수 없다. 출처 문서를 못 주면 명시적으로 `condition=`을 줘야 한다.
2. **이미지 안 라벨은 영어/숫자.** 한글 라벨은 폰트가 없는 환경(Linux CI)에서 두부 문자가 된다.
   해설은 마크다운 캡션에 한글로 쓴다.

SVG를 1차 산출물로 저장한다(텍스트라 diff가 되고 리뷰가 가능하다). PNG는 README 호환용.
"""
import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from palette import INK, SURFACE  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

# 이미지 안에 들어가도 되는 문자 (한글이 섞이면 폰트 문제로 깨진다)
_ASCII_OK = re.compile(r"^[\x20-\x7E]*$")


def assert_ascii(*texts: str) -> None:
    """이미지에 들어갈 문자열이 ASCII인지 확인 — 폰트 부재로 인한 두부 문자 방지."""
    for t in texts:
        if t and not _ASCII_OK.match(t):
            raise ValueError(
                f"이미지 안 라벨은 ASCII여야 한다 (폰트 부재 시 두부 문자): {t!r}. "
                "해설은 마크다운 캡션에 한글로 쓸 것 (VISUALIZATION_PLAN §2.3)."
            )


def condition_line(doc: dict | None, extra: str = "") -> str:
    """결과 문서에서 측정 조건 한 줄을 만든다."""
    if doc is None:
        return extra
    c = doc.get("contract") or {}
    parts = []
    if doc.get("git_commit"):
        parts.append(f"commit {doc['git_commit']}")
    if c:
        parts.append("obs{obs_version}/act{action_version}/rew{reward_version}/sim{sim_version}"
                     .format(**{k: c.get(k, "?") for k in
                                ("obs_version", "action_version",
                                 "reward_version", "sim_version")}))
    if doc.get("seed") is not None:
        parts.append(f"seed {doc['seed']}")
    if extra:
        parts.append(extra)
    return "  |  ".join(parts)


def new_figure(nrows: int = 1, ncols: int = 1, figsize=(9, 5), mode: str = "light", **kw):
    """표면·잉크가 팔레트를 따르는 Figure/Axes를 만든다."""
    fig, axes = plt.subplots(nrows, ncols, figsize=figsize,
                             facecolor=SURFACE[mode], **kw)
    ink = INK[mode]
    for ax in (axes.flat if hasattr(axes, "flat") else [axes]):
        ax.set_facecolor(SURFACE[mode])
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)          # 뼈대는 뒤로 물린다
        for side in ("left", "bottom"):
            ax.spines[side].set_color(ink["axis"])
        ax.tick_params(colors=ink["muted"], labelsize=9)
        ax.title.set_color(ink["primary"])
        ax.xaxis.label.set_color(ink["secondary"])
        ax.yaxis.label.set_color(ink["secondary"])
    return fig, axes


def save(fig, name: str, source_doc: dict | None = None,
         condition: str = "", mode: str = "light") -> list[str]:
    """SVG + PNG로 저장하고 조건을 그림 아래에 찍는다.

    source_doc: 결과 JSON을 그대로 넘기면 commit/seed/contract를 읽어 스탬프한다.
    condition : 결과 문서가 없거나 덧붙일 조건이 있을 때.
    """
    line = condition_line(source_doc, condition)
    if not line:
        raise ValueError(
            f"{name}: 측정 조건이 없다. source_doc(결과 JSON)이나 condition= 중 하나는 필요하다 "
            "(VISUALIZATION_PLAN §1-2)."
        )
    assert_ascii(line)
    fig.text(0.01, 0.012, line, fontsize=7, color=INK[mode]["muted"], ha="left")
    fig.tight_layout(rect=(0, 0.045, 1, 1))

    os.makedirs(OUT_DIR, exist_ok=True)
    written = []
    for ext in ("svg", "png"):
        path = os.path.join(OUT_DIR, f"{name}.{ext}")
        fig.savefig(path, format=ext, dpi=140, facecolor=SURFACE[mode])
        written.append(path)
    plt.close(fig)
    print(f"  wrote {name}.svg + .png  [{line}]", flush=True)
    return written


def label_bars(ax, bars, values, fmt="{:.2f}", mode: str = "light", pad=0.01):
    """막대 끝에 값을 직접 적는다.

    팔레트 검증기가 일부 슬롯에 대비 WARN을 냈고, 그 해소 조건이 '보이는 직접 라벨 또는 표'다.
    라벨은 계열 색이 아니라 잉크 토큰을 입는다 — 색이 정체성을 혼자 지지 않게.
    """
    span = max(values) if values else 1
    for bar, v in zip(bars, values):
        ax.text(bar.get_width() + span * pad, bar.get_y() + bar.get_height() / 2,
                fmt.format(v), va="center", ha="left",
                fontsize=9, color=INK[mode]["primary"])


if __name__ == "__main__":
    import json
    assert condition_line(None, "x") == "x"
    doc = {"git_commit": "abc1234", "seed": 42,
           "contract": {"obs_version": 1, "action_version": 1,
                        "reward_version": 1, "sim_version": 3}}
    line = condition_line(doc)
    assert "commit abc1234" in line and "sim3" in line and "seed 42" in line, line
    print("OK — 조건 스탬프:", line)

    try:
        assert_ascii("한글 라벨")
    except ValueError:
        print("OK — 한글 라벨 거부 (폰트 부재 대비)")
    else:
        raise AssertionError("한글 라벨이 통과됐다")

    fig, ax = new_figure(figsize=(4, 2))
    bars = ax.barh(["a", "b"], [1.0, 2.0])
    label_bars(ax, bars, [1.0, 2.0])
    out = save(fig, "_figspec_selftest", source_doc=doc)
    assert all(os.path.exists(p) for p in out), out
    for p in out:
        os.remove(p)
    print("OK — SVG+PNG 저장/삭제")

    fig, ax = new_figure(figsize=(3, 2))
    try:
        save(fig, "_no_condition")
    except ValueError:
        print("OK — 조건 없는 저장 거부")
    else:
        raise AssertionError("조건 없이 저장됐다")
    print("\n모든 자가 테스트 통과")
