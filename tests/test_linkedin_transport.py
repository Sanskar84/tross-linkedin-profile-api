from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import SecretStr

from tross_linkedin_api.clients.linkedin import (
    LinkedInRequestConfig,
    ProfilePageDocument,
    ProfilePageTransport,
    create_linkedin_config,
    create_linkedin_session,
    raise_for_upstream_status,
)
from tross_linkedin_api.config import LinkedInCookies, Settings
from tross_linkedin_api.errors import (
    LinkedInInvalidResponseError,
    LinkedInRateLimitedError,
    LinkedInSessionChallengedError,
    LinkedInUpstreamError,
)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        headers: Mapping[str, str] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.content = text.encode("utf-8")


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response


def test_request_config_contains_only_direct_http_transport_settings() -> None:
    settings = Settings(
        _env_file=None,
        linkedin_language="hi_IN",
        linkedin_restli_protocol_version="3.0.0",
        linkedin_request_timeout_seconds=12.5,
        linkedin_impersonate="chrome136",
    )

    config = create_linkedin_config(settings)

    assert config.language == "hi_IN"
    assert config.restli_protocol_version == "3.0.0"
    assert config.timeout_seconds == 12.5
    assert config.impersonate == "chrome136"
    assert set(LinkedInRequestConfig.model_fields) == {
        "base_url",
        "language",
        "restli_protocol_version",
        "impersonate",
        "timeout_seconds",
    }


@pytest.mark.asyncio
async def test_session_starts_with_only_li_at_and_never_follows_redirects() -> None:
    session = create_linkedin_session(
        cookies=LinkedInCookies(li_at=SecretStr("li-at-value")),
        config=LinkedInRequestConfig(),
    )

    try:
        assert session.base_url == "https://www.linkedin.com"
        assert session.impersonate == "chrome"
        assert session.allow_redirects is False
        assert session.trust_env is False
        assert session.verify is True
        assert "csrf-token" not in session.headers
        assert session.headers["x-restli-protocol-version"] == "2.0.0"
        assert session.headers["x-li-lang"] == "en_US"
        assert session.cookies.get("li_at") == "li-at-value"
        assert session.cookies.get("JSESSIONID") is None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_profile_page_uses_direct_authenticated_ssr_request() -> None:
    hydration_chunks = (
        '["0:[\\"$\\",\\"section\\",null,{\\"headline\\":\\"private-value\\"}]\\n",'
        '"1:I[123,[\\"module\\"],\\"default\\"]\\n"]'
    )
    html = """
    <html>
      <head>
        <meta name="pageKey" content="private-profile-value">
        <script type="application/json"></script>
      </head>
      <body>
        com.linkedin.sdui.generated.profile.dsl.impl.profileCardsAboveActivity
        com.linkedin.sdui.generated.profile.dsl.impl.profileCardsActivity
        com.linkedin.sdui.requests.profile.fetchProfileDiscoveryDrawer
        /flagship-web/rsc-action/actions/component
        /flagship-web/rsc-action/actions/server-request
        <script>
          window.__como_rehydration__ = HYDRATION_PLACEHOLDER;
        </script>
      </body>
    </html>
    """.replace("HYDRATION_PLACEHOLDER", hydration_chunks)
    session = FakeSession(
        FakeResponse(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=html,
        )
    )

    document = await ProfilePageTransport(session).fetch_profile_page("ada-lovelace")
    summary = document.summarize("ada-lovelace")

    assert session.calls == [
        {
            "url": "/in/ada-lovelace/",
            "headers": {
                "accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                )
            },
            "allow_redirects": False,
        }
    ]
    assert summary.content_type == "text/html; charset=utf-8"
    assert summary.content_length_bytes == len(html.encode("utf-8"))
    assert summary.profile_identifier_present is False
    assert summary.meta_keys == ["pageKey"]
    assert summary.script_types == ["application/json"]
    assert summary.sdui_identifiers == [
        "com.linkedin.sdui.generated.profile.dsl.impl.profileCardsAboveActivity",
        "com.linkedin.sdui.generated.profile.dsl.impl.profileCardsActivity",
        "com.linkedin.sdui.requests.profile.fetchProfileDiscoveryDrawer",
    ]
    assert summary.rsc_action_paths == [
        "/flagship-web/rsc-action/actions/component",
        "/flagship-web/rsc-action/actions/server-request",
    ]
    assert summary.como_hydration.present is True
    assert summary.como_hydration.chunk_count == 2
    assert summary.como_hydration.record_count == 2
    assert summary.como_hydration.record_tags == {"[": 1, "I": 1}
    assert "private-profile-value" not in summary.model_dump_json()
    assert "private-value" not in summary.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["experience", "projects", "skills"])
async def test_profile_details_page_uses_validated_direct_request(section: str) -> None:
    html = '<script>window.__como_rehydration__ = ["0:{}\\n"];</script>'
    session = FakeSession(
        FakeResponse(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=html,
        )
    )

    document = await ProfilePageTransport(session).fetch_profile_details_page(
        "ada-lovelace",
        section,
    )

    assert document.html == html
    assert session.calls == [
        {
            "url": f"/in/ada-lovelace/details/{section}/",
            "headers": {
                "accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                )
            },
            "allow_redirects": False,
        }
    ]


@pytest.mark.asyncio
async def test_profile_details_page_rejects_unknown_section() -> None:
    session = FakeSession(FakeResponse(200))

    with pytest.raises(ValueError, match="profile details section"):
        await ProfilePageTransport(session).fetch_profile_details_page(
            "ada-lovelace",
            "../../feed",
        )

    assert session.calls == []


def test_profile_page_summary_tolerates_missing_or_malformed_hydration() -> None:
    missing = ProfilePageDocument("<html></html>", "text/html", 13).summarize(
        "ada-lovelace"
    )
    malformed = ProfilePageDocument(
        "<script>window.__como_rehydration__ = not_json;</script>",
        "text/html",
        59,
    ).summarize("ada-lovelace")

    assert missing.como_hydration.present is False
    assert malformed.como_hydration.present is True
    assert malformed.como_hydration.chunk_count == 0
    assert malformed.como_hydration.record_count == 0


@pytest.mark.asyncio
async def test_profile_page_rejects_invalid_identifier_without_request() -> None:
    session = FakeSession(FakeResponse(200))

    with pytest.raises(ValueError, match="public identifier"):
        await ProfilePageTransport(session).fetch_profile_page("invalid/path")

    assert session.calls == []


@pytest.mark.asyncio
async def test_profile_page_rejects_non_html_response() -> None:
    session = FakeSession(
        FakeResponse(200, headers={"content-type": "application/json"}, text="{}")
    )

    with pytest.raises(LinkedInInvalidResponseError):
        await ProfilePageTransport(session).fetch_profile_page("ada-lovelace")


@pytest.mark.parametrize("status_code", [401, 403])
def test_authentication_failure_requires_cookie_refresh(status_code: int) -> None:
    with pytest.raises(LinkedInSessionChallengedError):
        raise_for_upstream_status(FakeResponse(status_code))


def test_checkpoint_redirect_requires_cookie_refresh() -> None:
    response = FakeResponse(
        302,
        headers={"location": "https://www.linkedin.com/checkpoint/challenge/123"},
    )

    with pytest.raises(LinkedInSessionChallengedError):
        raise_for_upstream_status(response)


def test_rate_limit_is_reported_separately() -> None:
    with pytest.raises(LinkedInRateLimitedError):
        raise_for_upstream_status(FakeResponse(429))


@pytest.mark.parametrize("status_code", [302, 500])
def test_other_upstream_failures_are_reported(status_code: int) -> None:
    response = FakeResponse(status_code, headers={"location": "/feed/"})

    with pytest.raises(LinkedInUpstreamError):
        raise_for_upstream_status(response)
