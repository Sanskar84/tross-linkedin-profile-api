"""Domain errors exposed through a stable API error envelope."""


class ApplicationError(Exception):
    """Base class for expected, client-safe application errors."""

    code = "APPLICATION_ERROR"
    message = "The request could not be completed."
    status_code = 500

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.message
        super().__init__(self.message)


class InvalidLinkedInCredentialError(ApplicationError):
    """Raised when a caller supplies a malformed LinkedIn session credential."""

    code = "INVALID_LINKEDIN_CREDENTIAL"
    message = "The LinkedIn session credential must use the Bearer scheme."
    status_code = 401


class LinkedInSessionChallengedError(ApplicationError):
    """Raised when LinkedIn redirects the session to a checkpoint."""

    code = "LINKEDIN_SESSION_CHALLENGED"
    message = "LinkedIn requires the configured session to be refreshed manually."
    status_code = 502


class LinkedInRateLimitedError(ApplicationError):
    """Raised when LinkedIn rate-limits the configured session."""

    code = "LINKEDIN_RATE_LIMITED"
    message = "LinkedIn rate-limited the configured session. Try again later."
    status_code = 503


class LinkedInUpstreamError(ApplicationError):
    """Raised for an unexpected response from LinkedIn."""

    code = "LINKEDIN_UPSTREAM_ERROR"
    message = "LinkedIn returned an unexpected response."
    status_code = 502


class LinkedInInvalidResponseError(ApplicationError):
    """Raised when LinkedIn returns an unrecognized response body."""

    code = "LINKEDIN_INVALID_RESPONSE"
    message = "LinkedIn returned an unrecognized response format."
    status_code = 502
