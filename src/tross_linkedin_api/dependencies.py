"""FastAPI dependency providers."""

import re
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header
from pydantic import SecretStr

from tross_linkedin_api.clients.linkedin import (
    ProfilePageTransport,
    create_linkedin_config,
    create_linkedin_session,
)
from tross_linkedin_api.clients.sdui import (
    SduiComponentTransport,
    SduiPaginationTransport,
)
from tross_linkedin_api.clients.ssr import SsrLinkedInProfileClient
from tross_linkedin_api.config import LinkedInCookies, Settings, settings
from tross_linkedin_api.errors import InvalidLinkedInCredentialError
from tross_linkedin_api.services.profile import ProfileService

_BEARER_TOKEN_PATTERN = re.compile(r"^[\x21-\x7e]{1,4096}$")


def resolve_linkedin_cookies(
    authorization: str | None,
    application_settings: Settings,
) -> LinkedInCookies:
    """Select a request-scoped li_at token or fall back to server configuration."""

    if authorization is None:
        return application_settings.linkedin_cookies()

    scheme, separator, token = authorization.partition(" ")
    if (
        separator != " "
        or scheme.casefold() != "bearer"
        or _BEARER_TOKEN_PATTERN.fullmatch(token) is None
    ):
        raise InvalidLinkedInCredentialError

    return LinkedInCookies(li_at=SecretStr(token))


def get_linkedin_cookies(
    authorization: Annotated[
        str | None,
        Header(
            alias="Authorization",
            description="Optional `Bearer <li_at>` credential for this request only.",
        ),
    ] = None,
) -> LinkedInCookies:
    """Resolve the optional caller credential without persisting its value."""

    return resolve_linkedin_cookies(authorization, settings)


LinkedInCookiesDependency = Annotated[LinkedInCookies, Depends(get_linkedin_cookies)]


async def get_profile_service(
    cookies: LinkedInCookiesDependency,
) -> AsyncIterator[ProfileService]:
    """Create one cookie-backed browserless LinkedIn session per API request."""

    config = create_linkedin_config(settings)
    session = create_linkedin_session(cookies, config)
    try:
        transport = ProfilePageTransport(session)
        component_transport = SduiComponentTransport(session)
        pagination_transport = SduiPaginationTransport(session)
        yield ProfileService(
            SsrLinkedInProfileClient(
                transport,
                component_transport,
                transport,
                pagination_transport,
            )
        )
    finally:
        await session.close()


ProfileServiceDependency = Annotated[ProfileService, Depends(get_profile_service)]
