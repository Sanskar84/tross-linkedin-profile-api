import json
from collections.abc import Mapping
from typing import Any

import pytest

from tross_linkedin_api.clients.sdui import (
    SduiComponentTransport,
    SduiPaginationTransport,
)
from tross_linkedin_api.errors import LinkedInSessionChallengedError
from tross_linkedin_api.parsers.como import (
    SduiComponentRequest,
    SduiPaginationRequest,
)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        text: str = "",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.content = text.encode()
        self.headers = headers or {}


class FakeSession:
    def __init__(
        self,
        response: FakeResponse,
        cookies: Mapping[str, str] | None = None,
    ) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []
        self.cookies = dict(cookies or {})

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response


@pytest.mark.asyncio
async def test_component_transport_posts_client_arguments_and_parses_flight() -> None:
    session = FakeSession(
        FakeResponse(
            200,
            '0:["$","div",null,{"children":["Experience"]}]\n',
            {"content-type": "application/octet-stream"},
        ),
        {"JSESSIONID": '"ajax:bootstrapped"'},
    )
    request = SduiComponentRequest(
        component_id="com.linkedin.sdui.profileCardsExperienceOnly",
        requested_arguments={
            "payload": {"vanityName": "ada-lovelace"},
            "requestMetadata": {"$type": "RequestMetadata"},
        },
    )

    document = await SduiComponentTransport(session).fetch_component(request)

    assert document.record_count == 1
    assert session.calls == [
        {
            "url": (
                "/flagship-web/rsc-action/actions/component?"
                "componentId=com.linkedin.sdui.profileCardsExperienceOnly&"
                "sduiid=com.linkedin.sdui.profileCardsExperienceOnly"
            ),
            "headers": {
                "accept": "text/x-component",
                "content-type": "application/json",
                "csrf-token": "ajax:bootstrapped",
            },
            "data": json.dumps(
                {"clientArguments": request.client_arguments()},
                separators=(",", ":"),
            ),
            "allow_redirects": False,
        }
    ]


@pytest.mark.asyncio
async def test_component_transport_detects_checkpoint_redirect() -> None:
    session = FakeSession(
        FakeResponse(302, headers={"location": "/checkpoint/challenge/123"}),
        {"JSESSIONID": '"ajax:bootstrapped"'},
    )
    request = SduiComponentRequest("component.id", {})

    with pytest.raises(LinkedInSessionChallengedError):
        await SduiComponentTransport(session).fetch_component(request)


@pytest.mark.asyncio
async def test_component_transport_requires_bootstrapped_jsessionid() -> None:
    session = FakeSession(FakeResponse(200, "0:{}\n"))
    request = SduiComponentRequest("component.id", {})

    with pytest.raises(LinkedInSessionChallengedError):
        await SduiComponentTransport(session).fetch_component(request)

    assert session.calls == []


@pytest.mark.asyncio
async def test_pagination_transport_posts_embedded_request_and_parses_flight() -> None:
    session = FakeSession(
        FakeResponse(
            200,
            '0:["$","div",null,{"children":["Python"]}]\n',
            {"content-type": "application/octet-stream"},
        ),
        {"JSESSIONID": '"ajax:bootstrapped"'},
    )
    raw_request = {
        "$type": "proto.sdui.actions.requests.PaginationRequest",
        "pagerId": "com.linkedin.sdui.pagers.profile.details.skills",
        "requestedArguments": {
            "$type": "proto.sdui.actions.requests.RequestedArguments",
            "payload": {
                "vanityName": "ada-lovelace",
                "start": 0,
                "count": 10,
                "filter": "ProfileSkillCategory_ALL",
            },
            "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
        },
    }
    request = SduiPaginationRequest(
        pager_id="com.linkedin.sdui.pagers.profile.details.skills",
        requested_arguments=raw_request["requestedArguments"],
        raw_request=raw_request,
    )
    screen_id = "com.linkedin.sdui.flagshipnav.profile.ProfileSkillDetails"

    document = await SduiPaginationTransport(session).fetch_page(request, screen_id)

    assert document.record_count == 1
    assert session.calls == [
        {
            "url": "/flagship-web/rsc-action/actions/pagination",
            "headers": {
                "accept": "text/x-component",
                "content-type": "application/json",
                "csrf-token": "ajax:bootstrapped",
            },
            "data": json.dumps(
                {
                    "pagerId": request.pager_id,
                    "clientArguments": request.client_arguments(screen_id),
                    "paginationRequest": request.raw_request,
                },
                separators=(",", ":"),
            ),
            "allow_redirects": False,
        }
    ]
