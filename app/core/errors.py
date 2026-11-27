"""Errores con el formato RFC 7807 (application/problem+json), como Auth-User y Server-Roles."""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """
    Error de negocio. `code` es estable y en inglés (el cliente decide qué mostrar con él); `detail` es para
    personas. `extra` va como campos de extensión del problem (p. ej. `reason` de un pago rechazado).
    """

    def __init__(self, status_code: int, code: str, detail: str, **extra: Any):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.extra = extra


def problem_response(
    request: Request, status_code: int, detail: str, code: str | None = None, **extra: Any
) -> JSONResponse:
    content: dict[str, Any] = {
        "type": "about:blank",
        "title": HTTPStatus(status_code).phrase,
        "status": status_code,
        "detail": detail,
        "instance": request.url.path,
    }
    if code:
        content["code"] = code
    content.update(extra)
    return JSONResponse(status_code=status_code, content=content, media_type="application/problem+json")


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return problem_response(request, exc.status_code, exc.detail, exc.code, **exc.extra)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    invalid_params = [
        {"name": ".".join(str(loc) for loc in err["loc"] if loc != "body") or "payload", "reason": err["msg"]}
        for err in exc.errors()
    ]
    return problem_response(
        request,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "Hay datos inválidos en el pedido.",
        "validation_error",
        invalid_params=invalid_params,
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return problem_response(request, exc.status_code, str(exc.detail))


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
