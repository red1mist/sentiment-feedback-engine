from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..db import connect
from ..schemas import HealthOut, MetricsOut

router = APIRouter()


@router.get("/healthz", response_model=HealthOut, responses={503: {"model": HealthOut}})
def healthz(request: Request):
    try:
        conn = connect(request.app.state.settings.database_path)
        try:
            conn.execute("SELECT 1 FROM feedback LIMIT 1")
        finally:
            conn.close()
        database = "ok"
    except Exception:  # noqa: BLE001
        database = "down"
    body = HealthOut(status="ok" if database == "ok" else "degraded", database=database,
                     model=request.app.state.classifier.name)
    return JSONResponse(status_code=200 if database == "ok" else 503, content=body.model_dump())


@router.get("/metrics", response_model=MetricsOut)
def metrics(request: Request) -> MetricsOut:
    return request.app.state.metrics.snapshot()
