import os

from fastapi import FastAPI
import uvicorn

from recovery_lab.routes import router

app = FastAPI(title="Vagus 복구 실험실", description="공통 복구 연구 정책을 사용하는 탐색 실험 API")
app.include_router(router)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("RECOVERY_LAB_PORT", "8000")))
