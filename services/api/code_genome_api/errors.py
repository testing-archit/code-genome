from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str) -> None:
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail


def _problem(
    request: Request, status: int, code: str, title: str, detail: str, errors: Any = None
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://codegenome.dev/problems/{code.lower()}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": str(request.url.path),
        "request_id": getattr(request.state, "request_id", "unknown"),
        "code": code,
    }
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return _problem(request, exc.status, exc.code, exc.title, exc.detail)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return _problem(
        request,
        422,
        "VALIDATION_FAILED",
        "Request validation failed",
        "One or more request fields are invalid.",
        errors,
    )
