import pytest
from pydantic import SecretStr

from tross_linkedin_api.config import LinkedInCookies, MissingLinkedInCookiesError, Settings


def test_li_at_produces_minimal_cookie_set() -> None:
    settings = Settings(
        _env_file=None,
        linkedin_li_at=SecretStr("li-at-value"),
    )

    cookies = settings.linkedin_cookies()

    assert cookies.as_dict() == {"li_at": "li-at-value"}


def test_missing_li_at_reports_required_variable_name() -> None:
    settings = Settings(_env_file=None)

    with pytest.raises(MissingLinkedInCookiesError) as exc_info:
        settings.linkedin_cookies()

    message = str(exc_info.value)
    assert "LINKEDIN_LI_AT" in message


def test_cookie_model_masks_secrets_in_repr() -> None:
    cookies = LinkedInCookies(
        li_at=SecretStr("secret-a"),
    )

    representation = repr(cookies)

    assert "secret-a" not in representation
