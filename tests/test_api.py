from fastapi import FastAPI
from fastapi.testclient import TestClient

from tross_linkedin_api.dependencies import get_profile_service
from tross_linkedin_api.errors import LinkedInUpstreamError
from tross_linkedin_api.main import create_app
from tross_linkedin_api.schemas.profile import LinkedInProfile, ProfileRequest
from tross_linkedin_api.services.profile import ProfileService


class FailingLinkedInClient:
    async def fetch_profile(self, request: ProfileRequest) -> LinkedInProfile:
        del request
        raise LinkedInUpstreamError


def create_failing_test_app() -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_profile_service] = lambda: ProfileService(
        FailingLinkedInClient()
    )
    return app


def test_health_endpoint_does_not_require_linkedin_credentials() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_homepage_exposes_accessible_profile_demo() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<label for="profile-url">LinkedIn profile URL</label>' in response.text
    assert 'action="/v1/linkedin/profile"' in response.text
    assert 'aria-live="polite"' in response.text
    assert 'href="/docs"' in response.text


def test_profile_endpoint_exposes_stable_upstream_error() -> None:
    with TestClient(create_failing_test_app()) as client:
        response = client.post(
            "/v1/linkedin/profile",
            json={"profile_url": "https://www.linkedin.com/in/ada-lovelace/"},
        )

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "LINKEDIN_UPSTREAM_ERROR",
            "message": "LinkedIn returned an unexpected response.",
        }
    }


def test_profile_endpoint_rejects_invalid_url() -> None:
    with TestClient(create_failing_test_app()) as client:
        response = client.post(
            "/v1/linkedin/profile",
            json={"profile_url": "https://example.com/in/ada-lovelace"},
        )

    assert response.status_code == 422


def test_profile_endpoint_documents_optional_authorization_header() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/openapi.json")

    operation = response.json()["paths"]["/v1/linkedin/profile"]["post"]
    authorization_parameters = [
        parameter
        for parameter in operation["parameters"]
        if parameter["name"] == "Authorization" and parameter["in"] == "header"
    ]

    assert len(authorization_parameters) == 1
    assert authorization_parameters[0]["required"] is False
    assert "401" in operation["responses"]


def test_profile_endpoint_rejects_malformed_authorization_without_fallback() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/linkedin/profile",
            headers={"Authorization": "Basic must-not-be-reflected"},
            json={"profile_url": "https://www.linkedin.com/in/ada-lovelace/"},
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "INVALID_LINKEDIN_CREDENTIAL",
            "message": "The LinkedIn session credential must use the Bearer scheme.",
        }
    }
    assert "must-not-be-reflected" not in response.text
