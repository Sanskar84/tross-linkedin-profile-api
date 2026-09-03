"""Direct browserless HTTP transport for LinkedIn profile pages."""

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession, Response
from pydantic import BaseModel, ConfigDict, Field

from tross_linkedin_api.config import LinkedInCookies, Settings
from tross_linkedin_api.errors import (
    LinkedInInvalidResponseError,
    LinkedInRateLimitedError,
    LinkedInSessionChallengedError,
    LinkedInUpstreamError,
)
from tross_linkedin_api.schemas.profile import PUBLIC_IDENTIFIER_PATTERN


class LinkedInRequestConfig(BaseModel):
    """Changeable settings for direct LinkedIn HTTP requests."""

    model_config = ConfigDict(frozen=True)

    base_url: str = "https://www.linkedin.com"
    language: str = "en_US"
    restli_protocol_version: str = "2.0.0"
    impersonate: str = "chrome"
    timeout_seconds: float = Field(default=30.0, gt=0, le=120)


class UpstreamResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]


class ProfilePageResponse(UpstreamResponse, Protocol):
    content: bytes
    text: str


class ProfilePageSession(Protocol):
    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        allow_redirects: bool,
    ) -> ProfilePageResponse:
        """Issue a GET request without following redirects."""
        ...


def create_linkedin_config(settings: Settings) -> LinkedInRequestConfig:
    """Map environment-backed settings to the direct HTTP transport."""

    return LinkedInRequestConfig(
        language=settings.linkedin_language,
        restli_protocol_version=settings.linkedin_restli_protocol_version,
        impersonate=settings.linkedin_impersonate,
        timeout_seconds=settings.linkedin_request_timeout_seconds,
    )


def create_linkedin_session(
    cookies: LinkedInCookies,
    config: LinkedInRequestConfig,
) -> AsyncSession[Response]:
    """Create a persistent browserless LinkedIn HTTP session."""

    return AsyncSession(
        base_url=config.base_url,
        headers={
            "x-restli-protocol-version": config.restli_protocol_version,
            "x-li-lang": config.language,
        },
        cookies=cookies.as_dict(),
        impersonate=config.impersonate,
        allow_redirects=False,
        timeout=config.timeout_seconds,
        trust_env=False,
        verify=True,
    )


PROFILE_PAGE_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,*/*;q=0.8"
)
PROFILE_DETAILS_SECTIONS = frozenset(
    {
        "certifications",
        "courses",
        "education",
        "experience",
        "honors",
        "organizations",
        "projects",
        "publications",
        "recommendations",
        "skills",
        "test-scores",
        "volunteering-experiences",
    }
)
SDUI_IDENTIFIER_PATTERN = re.compile(r"com\.linkedin\.sdui\.[A-Za-z0-9_.]+")
RSC_ACTION_PATHS = (
    "/flagship-web/rsc-action/actions/component",
    "/flagship-web/rsc-action/actions/server-request",
)


class ProfilePageSummary(BaseModel):
    """Non-sensitive structural facts about an SSR profile response."""

    content_type: str
    content_length_bytes: int
    profile_identifier_present: bool
    meta_keys: list[str]
    script_types: list[str]
    sdui_identifiers: list[str]
    rsc_action_paths: list[str]
    como_hydration: "ComoHydrationSummary"


class ComoHydrationSummary(BaseModel):
    """Structural metadata only; profile values are deliberately excluded."""

    present: bool
    chunk_count: int = 0
    stream_length_chars: int = 0
    record_count: int = 0
    record_tags: dict[str, int] = Field(default_factory=dict)


class _StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta_keys: set[str] = set()
        self.script_types: set[str] = set()

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if tag == "meta":
            key = attributes.get("name") or attributes.get("property")
            if key:
                self.meta_keys.add(key)
        elif tag == "script":
            script_type = attributes.get("type")
            if script_type:
                self.script_types.add(script_type)


@dataclass(frozen=True, repr=False)
class ProfilePageDocument:
    """Raw SSR HTML retained only for local parsing and reverse engineering."""

    html: str
    content_type: str
    content_length_bytes: int

    def summarize(self, public_identifier: str) -> ProfilePageSummary:
        parser = _StructureParser()
        parser.feed(self.html)
        return ProfilePageSummary(
            content_type=self.content_type,
            content_length_bytes=self.content_length_bytes,
            profile_identifier_present=(
                public_identifier.casefold() in self.html.casefold()
            ),
            meta_keys=sorted(parser.meta_keys),
            script_types=sorted(parser.script_types),
            sdui_identifiers=sorted(set(SDUI_IDENTIFIER_PATTERN.findall(self.html))),
            rsc_action_paths=[path for path in RSC_ACTION_PATHS if path in self.html],
            como_hydration=_summarize_como_hydration(self.html),
        )


COMO_REHYDRATION_MARKER = "window.__como_rehydration__"
FLIGHT_RECORD_PATTERN = re.compile(r"^[0-9a-f]+:(.)?", re.IGNORECASE)


def _summarize_como_hydration(html: str) -> ComoHydrationSummary:
    marker_index = html.find(COMO_REHYDRATION_MARKER)
    if marker_index < 0:
        return ComoHydrationSummary(present=False)

    assignment_index = html.find("=", marker_index + len(COMO_REHYDRATION_MARKER))
    if assignment_index < 0:
        return ComoHydrationSummary(present=True)

    encoded_chunks = html[assignment_index + 1 :].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(encoded_chunks)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ComoHydrationSummary(present=True)

    if not isinstance(value, list) or not all(isinstance(chunk, str) for chunk in value):
        return ComoHydrationSummary(present=True)

    stream = "".join(value)
    tags: Counter[str] = Counter()
    record_count = 0
    for line in stream.split("\n"):
        match = FLIGHT_RECORD_PATTERN.match(line.removesuffix("\r"))
        if match is None:
            continue
        record_count += 1
        tags[match.group(1) or "<empty>"] += 1

    return ComoHydrationSummary(
        present=True,
        chunk_count=len(value),
        stream_length_chars=len(stream),
        record_count=record_count,
        record_tags=dict(sorted(tags.items())),
    )


class ProfilePageTransport:
    """Retrieve an authenticated server-rendered profile document directly."""

    def __init__(
        self,
        session: ProfilePageSession | AsyncSession[Response],
    ) -> None:
        self._session = session

    async def fetch_profile_page(self, public_identifier: str) -> ProfilePageDocument:
        if not PUBLIC_IDENTIFIER_PATTERN.fullmatch(public_identifier):
            raise ValueError("Invalid LinkedIn public identifier")

        return await self._fetch_html(f"/in/{public_identifier}/")

    async def fetch_profile_details_page(
        self,
        public_identifier: str,
        section: str,
    ) -> ProfilePageDocument:
        """Retrieve a validated server-rendered profile details page."""

        if not PUBLIC_IDENTIFIER_PATTERN.fullmatch(public_identifier):
            raise ValueError("Invalid LinkedIn public identifier")
        if section not in PROFILE_DETAILS_SECTIONS:
            raise ValueError("Invalid LinkedIn profile details section")

        return await self._fetch_html(f"/in/{public_identifier}/details/{section}/")

    async def _fetch_html(self, path: str) -> ProfilePageDocument:
        """Retrieve and validate one authenticated LinkedIn HTML document."""

        response = await self._session.get(
            path,
            headers={"accept": PROFILE_PAGE_ACCEPT},
            allow_redirects=False,
        )
        raise_for_upstream_status(response)

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type.casefold() or not response.text.strip():
            raise LinkedInInvalidResponseError

        return ProfilePageDocument(
            html=response.text,
            content_type=content_type,
            content_length_bytes=len(response.content),
        )


def raise_for_upstream_status(response: UpstreamResponse | Response) -> None:
    location = response.headers.get("location") or response.headers.get("Location")
    if (
        300 <= response.status_code < 400
        and location
        and urlparse(location).path.startswith("/checkpoint/")
    ):
        raise LinkedInSessionChallengedError

    if response.status_code in {401, 403}:
        raise LinkedInSessionChallengedError
    if response.status_code == 429:
        raise LinkedInRateLimitedError
    if response.status_code != 200:
        raise LinkedInUpstreamError
