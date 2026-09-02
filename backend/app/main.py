from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from .api.v1.router import api_v1
from .errors import install_error_handlers
from .logging_setup import configure_logging, new_request_id, request_id_var


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="Clinical Note Labeller",
        version="1.0.0",
        openapi_url="/api/v1/openapi.json",
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or new_request_id()
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers["X-API-Version"] = "v1"
        return response

    install_error_handlers(app)
    app.include_router(api_v1)

    @app.get("/api/v1/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
