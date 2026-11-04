from pathlib import Path
from typing import Final, Literal

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from recovery_research.policies import POLICIES
from recovery_research.scenarios import Regime, scenario_case
from .models import CreateRequest, Model, Overrides, SessionView, StepRequest
from .session import SessionError, SessionStore

router = APIRouter(prefix="/recovery", tags=["복구 실험실"])
STORE: Final = SessionStore()
WEB: Final = Path(__file__).resolve().parent / "web"
POLICY_LABELS: Final = (
    "고정 대기", "관측 검증", "10회 연속 관측", "수요 예측",
    "보정 + 고정 대기", "보정 + 관측 검증", "보정 + 10회 관측",
    "보정 + 수요 예측", "보정 + 예측 + 추가 안정성 확인",
)
PRESET_LABELS: Final = ("기본 조건", "수요 과소 관측", "관측 지연", "관측 누락", "부하 잡음", "배경 트래픽 경유")


class PolicyOption(Model):
    name: str
    label: str


class PresetOption(Model):
    name: Regime
    label: str
    values: Overrides


class Options(Model):
    policies: tuple[PolicyOption, ...]
    presets: tuple[PresetOption, ...]


@router.get("", include_in_schema=False)
def page() -> FileResponse:
    return FileResponse(WEB / "index.html")


@router.get("/assets/{name}", include_in_schema=False)
def asset(name: Literal["lab.css", "lab.js"]) -> FileResponse:
    return FileResponse(WEB / name)


@router.get("/options", response_model=Options)
def options() -> Options:
    presets: list[PresetOption] = []
    for regime, label in zip(Regime, PRESET_LABELS, strict=True):
        scenario = scenario_case(0, regime).scenario
        values = Overrides.model_validate({name: getattr(scenario, name) for name in Overrides.model_fields})
        presets.append(PresetOption(name=regime, label=label, values=values))
    return Options(policies=tuple(PolicyOption(name=p.name, label=label)
                                 for p, label in zip(POLICIES, POLICY_LABELS, strict=True)),
                   presets=tuple(presets))


@router.post("/sessions", response_model=SessionView, status_code=201)
def create_session(request: CreateRequest) -> SessionView:
    try:
        return STORE.create(request)
    except SessionError as error:
        raise HTTPException(error.status, error.detail) from error


@router.get("/sessions/{identity}", response_model=SessionView)
def session_state(identity: str) -> SessionView:
    try:
        return STORE.view(identity)
    except SessionError as error:
        raise HTTPException(error.status, error.detail) from error


@router.post("/sessions/{identity}/step", response_model=SessionView)
def step_session(identity: str, request: StepRequest) -> SessionView:
    try:
        return STORE.advance(identity, request.ticks)
    except SessionError as error:
        raise HTTPException(error.status, error.detail) from error


@router.delete("/sessions/{identity}", status_code=204)
def delete_session(identity: str) -> Response:
    try:
        STORE.delete(identity)
    except SessionError as error:
        raise HTTPException(error.status, error.detail) from error
    return Response(status_code=204)
