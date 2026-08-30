"""Public HTTP routes."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from tross_linkedin_api.api.homepage import HOMEPAGE_HTML
from tross_linkedin_api.dependencies import ProfileServiceDependency
from tross_linkedin_api.schemas.common import ErrorResponse, HealthResponse
from tross_linkedin_api.schemas.profile import ProfileRequest, ProfileResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def homepage() -> HTMLResponse:
    return HTMLResponse(HOMEPAGE_HTML)


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse()


@router.post(
    "/v1/linkedin/profile",
    response_model=ProfileResponse,
    responses={
        401: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["linkedin"],
)
async def retrieve_profile(
    payload: ProfileRequest,
    service: ProfileServiceDependency,
) -> ProfileResponse:
    return await service.retrieve(payload)
