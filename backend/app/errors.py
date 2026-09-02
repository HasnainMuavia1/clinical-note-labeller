from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"


class ProblemException(Exception):
    def __init__(self, status: int, title: str, detail: str, type_: str = "about:blank"):
        self.status, self.title, self.detail, self.type_ = status, title, detail, type_
        super().__init__(detail)


def problem(status: int, title: str, detail: str, type_: str = "about:blank", **extra) -> JSONResponse:
    body = {"type": type_, "title": title, "status": status, "detail": detail, **extra}
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemException)
    async def _problem(_: Request, exc: ProblemException):
        return problem(exc.status, exc.title, exc.detail, exc.type_)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        titles = {401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
                  405: "Method Not Allowed", 409: "Conflict", 429: "Too Many Requests"}
        return problem(exc.status_code, titles.get(exc.status_code, "Error"), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return problem(422, "Unprocessable Entity", "Request validation failed",
                       errors=[{k: str(v) for k, v in e.items()} for e in exc.errors()])

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        return problem(500, "Internal Server Error", "An unexpected error occurred")
