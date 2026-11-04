from fastapi import FastAPI
from fastapi.testclient import TestClient

from recovery_research.evaluation import run_episode
from recovery_research.policies import POLICIES
from recovery_research.scenarios import Regime, scenario_case
from recovery_lab.routes import router


def main() -> None:
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        options = client.get("/recovery/options")
        assert options.status_code == 200
        assert len(options.json()["policies"]) == 9
        assert len(options.json()["presets"]) == 6
        assert client.get("/recovery").status_code == 200
        assert client.get("/recovery/assets/lab.js").status_code == 200
        assert client.get("/recovery/assets/../../AGENTS.md").status_code != 200
        assert client.get("/recovery/sessions/unknown").status_code == 404
        assert client.post("/recovery/sessions", json={"policy": "unknown"}).status_code == 422
        assert client.post("/recovery/sessions", json={"overrides": {"missing_probability": 2}}).status_code == 422
        assert client.post("/recovery/sessions", json={"overrides": {"demand_bias": "nan"}}).status_code == 422
        sessions: list[str] = []
        for name in (p.name for p in POLICIES):
            request = {"policy": name, "regime": "demand_bias", "seed": 0}
            created = client.post("/recovery/sessions", json=request)
            assert created.status_code == 201, created.text
            session = created.json()
            identity = session["session_id"]
            sessions.append(identity)
            assert session["tick"] == 0 and session["exploratory"]
            assert client.get(f"/recovery/sessions/{identity}").json()["tick"] == 0
            assert client.post(f"/recovery/sessions/{identity}/step", json={"ticks": 0}).status_code == 422
            first = client.post(f"/recovery/sessions/{identity}/step", json={"ticks": 1})
            assert first.status_code == 200 and first.json()["tick"] == 1
            finished = client.post(f"/recovery/sessions/{identity}/step", json={"ticks": 120}).json()
            expected = run_episode(scenario_case(0, Regime.DEMAND_BIAS), next(p for p in POLICIES if p.name == name))
            assert finished["finished"] and finished["tick"] == 120
            assert finished["command_count"] == expected.command_count
            assert finished["unsafe_restore"] == expected.unsafe_restore
            assert finished["false_completion"] == expected.false_completion
            assert finished["declared_tick"] == expected.declared_tick
            assert abs(finished["overload_area"] - expected.overload_area) < 1e-9
            assert client.post(f"/recovery/sessions/{identity}/step", json={"ticks": 1}).status_code == 409
        assert client.get(f"/recovery/sessions/{sessions[0]}").json()["session_id"] != sessions[1]
        for identity in sessions:
            assert client.delete(f"/recovery/sessions/{identity}").status_code == 204
            assert client.get(f"/recovery/sessions/{identity}").status_code == 404
    print("복구 실험실 API·공통 연구 정책 일치 검증 통과")


if __name__ == "__main__":
    main()
