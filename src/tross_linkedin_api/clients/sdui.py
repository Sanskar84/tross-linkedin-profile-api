"""Direct HTTP transport for LinkedIn's lazy SDUI component endpoint."""

import json
import re
from collections.abc import Mapping
from typing import Protocol
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession, Response

from tross_linkedin_api.clients.linkedin import raise_for_upstream_status
from tross_linkedin_api.errors import (
    LinkedInInvalidResponseError,
    LinkedInSessionChallengedError,
)
from tross_linkedin_api.parsers.como import (
    ComoFlightDocument,
    ComoFlightParseError,
    SduiComponentRequest,
    SduiPaginationRequest,
    parse_flight_stream,
)

COMPONENT_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class ComponentResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    text: str


class ComponentCookies(Protocol):
    def get(self, name: str) -> str | None:
        """Return the current cookie value by name."""
        ...


class ComponentSession(Protocol):
    cookies: ComponentCookies

    async def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        data: str,
        allow_redirects: bool,
    ) -> ComponentResponse:
        """Issue a component POST without following redirects."""
        ...


class SduiComponentTransport:
    """Fetch React Flight component data using LinkedIn's embedded arguments."""

    def __init__(
        self,
        session: ComponentSession | AsyncSession[Response],
    ) -> None:
        self._session = session

    async def fetch_component(
        self,
        request: SduiComponentRequest,
    ) -> ComoFlightDocument:
        if not COMPONENT_IDENTIFIER_PATTERN.fullmatch(request.component_id):
            raise ValueError("Invalid SDUI component identifier")

        jsessionid = self._session.cookies.get("JSESSIONID")
        if not jsessionid or not jsessionid.strip('"'):
            raise LinkedInSessionChallengedError
        csrf_token = jsessionid.strip('"')

        query = urlencode(
            {
                "componentId": request.component_id,
                "sduiid": request.component_id,
            }
        )
        response = await self._session.post(
            f"/flagship-web/rsc-action/actions/component?{query}",
            headers={
                "accept": "text/x-component",
                "content-type": "application/json",
                "csrf-token": csrf_token,
            },
            data=json.dumps(
                {"clientArguments": request.client_arguments()},
                separators=(",", ":"),
            ),
            allow_redirects=False,
        )
        raise_for_upstream_status(response)
        if not response.text.strip():
            raise LinkedInInvalidResponseError
        try:
            return parse_flight_stream(response.text)
        except ComoFlightParseError as error:
            raise LinkedInInvalidResponseError from error


class SduiPaginationTransport:
    """Fetch a page from a LinkedIn SDUI pager using embedded arguments."""

    def __init__(
        self,
        session: ComponentSession | AsyncSession[Response],
    ) -> None:
        self._session = session

    async def fetch_page(
        self,
        request: SduiPaginationRequest,
        screen_id: str,
    ) -> ComoFlightDocument:
        if not COMPONENT_IDENTIFIER_PATTERN.fullmatch(request.pager_id):
            raise ValueError("Invalid SDUI pager identifier")
        if not COMPONENT_IDENTIFIER_PATTERN.fullmatch(screen_id):
            raise ValueError("Invalid SDUI screen identifier")

        jsessionid = self._session.cookies.get("JSESSIONID")
        if not jsessionid or not jsessionid.strip('"'):
            raise LinkedInSessionChallengedError
        csrf_token = jsessionid.strip('"')

        response = await self._session.post(
            "/flagship-web/rsc-action/actions/pagination",
            headers={
                "accept": "text/x-component",
                "content-type": "application/json",
                "csrf-token": csrf_token,
            },
            data=json.dumps(
                {
                    "pagerId": request.pager_id,
                    "clientArguments": request.client_arguments(screen_id),
                    "paginationRequest": request.raw_request,
                },
                separators=(",", ":"),
            ),
            allow_redirects=False,
        )
        raise_for_upstream_status(response)
        if not response.text.strip():
            raise LinkedInInvalidResponseError
        try:
            return parse_flight_stream(response.text)
        except ComoFlightParseError as error:
            raise LinkedInInvalidResponseError from error
