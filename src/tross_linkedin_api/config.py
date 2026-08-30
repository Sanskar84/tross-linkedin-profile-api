"""Application configuration and secret-safe LinkedIn cookie handling."""

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MissingLinkedInCookiesError(ValueError):
    """Raised when the required LinkedIn authentication cookie is missing."""


class LinkedInCookies(BaseModel):
    """The minimal cookie required to bootstrap a LinkedIn session.

    Values remain wrapped in ``SecretStr`` until the HTTP transport needs them.
    """

    li_at: SecretStr

    def as_dict(self) -> dict[str, str]:
        """Return cookie names and raw values for the HTTP transport only."""

        return {"li_at": self.li_at.get_secret_value()}


class Settings(BaseSettings):
    """Environment-backed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Tross LinkedIn Profile API"
    app_version: str = "0.1.0"

    linkedin_li_at: SecretStr | None = None
    linkedin_impersonate: str = "chrome"
    linkedin_language: str = "en_US"
    linkedin_restli_protocol_version: str = "2.0.0"
    linkedin_request_timeout_seconds: float = 30.0

    def linkedin_cookies(self) -> LinkedInCookies:
        """Build the session cookie set, requiring only the authentication token."""

        if self.linkedin_li_at is None or not self.linkedin_li_at.get_secret_value().strip():
            raise MissingLinkedInCookiesError(
                "Missing LinkedIn cookie: LINKEDIN_LI_AT"
            )

        assert self.linkedin_li_at is not None
        return LinkedInCookies(li_at=self.linkedin_li_at)


settings = Settings()
