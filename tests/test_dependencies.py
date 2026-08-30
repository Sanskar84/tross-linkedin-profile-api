import pytest
from pydantic import SecretStr

from tross_linkedin_api.config import Settings
from tross_linkedin_api.dependencies import resolve_linkedin_cookies
from tross_linkedin_api.errors import InvalidLinkedInCredentialError


def test_request_bearer_cookie_overrides_backend_cookie() -> None:
    application_settings = Settings(
        _env_file=None,
        linkedin_li_at=SecretStr("backend-cookie"),
    )

    cookies = resolve_linkedin_cookies(
        "Bearer caller-cookie",
        application_settings,
    )

    assert cookies.as_dict() == {"li_at": "caller-cookie"}


def test_missing_authorization_uses_backend_cookie() -> None:
    application_settings = Settings(
        _env_file=None,
        linkedin_li_at=SecretStr("backend-cookie"),
    )

    cookies = resolve_linkedin_cookies(None, application_settings)

    assert cookies.as_dict() == {"li_at": "backend-cookie"}


@pytest.mark.parametrize(
    "authorization",
    [
        "",
        "Basic caller-cookie",
        "Bearer",
        "Bearer ",
        "Bearer caller-cookie extra",
        "Bearer caller\tcookie",
        f"Bearer {'x' * 4097}",
    ],
)
def test_invalid_authorization_is_rejected_without_exposing_value(
    authorization: str,
) -> None:
    application_settings = Settings(
        _env_file=None,
        linkedin_li_at=SecretStr("backend-cookie"),
    )

    with pytest.raises(InvalidLinkedInCredentialError) as exc_info:
        resolve_linkedin_cookies(authorization, application_settings)

    assert str(exc_info.value) == (
        "The LinkedIn session credential must use the Bearer scheme."
    )
    assert exc_info.value.status_code == 401


def test_bearer_scheme_is_case_insensitive() -> None:
    application_settings = Settings(_env_file=None)

    cookies = resolve_linkedin_cookies("bearer caller-cookie", application_settings)

    assert cookies.as_dict() == {"li_at": "caller-cookie"}
