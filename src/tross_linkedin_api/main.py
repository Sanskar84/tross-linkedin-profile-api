"""FastAPI application factory."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tross_linkedin_api.api.routes import router
from tross_linkedin_api.config import settings
from tross_linkedin_api.errors import ApplicationError
from tross_linkedin_api.schemas.common import ErrorDetail, ErrorResponse


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Browserless LinkedIn profile retrieval API.",
    )

    @app.exception_handler(ApplicationError)
    async def handle_application_error(
        request: Request,
        error: ApplicationError,
    ) -> JSONResponse:
        del request
        payload = ErrorResponse(
            error=ErrorDetail(code=error.code, message=error.message)
        )
        return JSONResponse(
            status_code=error.status_code,
            content=payload.model_dump(),
        )

    app.include_router(router)
    return app


app = create_app()

