"""Profile client backed by LinkedIn's authenticated SSR response."""

import json
from typing import Protocol

from tross_linkedin_api.clients.linkedin import ProfilePageDocument
from tross_linkedin_api.errors import LinkedInInvalidResponseError
from tross_linkedin_api.parsers.como import (
    ComoFlightDocument,
    ComoFlightParseError,
    SduiComponentRequest,
    SduiPaginationRequest,
    extract_about_from_flight,
    extract_certifications_from_flight,
    extract_component_requests,
    extract_education_from_flight,
    extract_education_pagination_request,
    extract_experiences_from_flight,
    extract_languages_from_flight,
    extract_profile_details_path,
    extract_profile_from_como,
    extract_projects_from_flight,
    extract_projects_pagination_request,
    extract_skills_details_path,
    extract_skills_from_flight,
    extract_skills_pagination_request,
    parse_como_flight,
)
from tross_linkedin_api.schemas.profile import (
    Education,
    LinkedInProfile,
    Position,
    ProfileRequest,
    Project,
)


class ProfilePageFetcher(Protocol):
    async def fetch_profile_page(self, public_identifier: str) -> ProfilePageDocument:
        """Retrieve one authenticated server-rendered profile page."""
        ...


class ComponentFetcher(Protocol):
    async def fetch_component(
        self,
        request: SduiComponentRequest,
    ) -> ComoFlightDocument:
        """Retrieve one lazy SDUI component response."""
        ...


class ProfileDetailsFetcher(Protocol):
    async def fetch_profile_details_page(
        self,
        public_identifier: str,
        section: str,
    ) -> ProfilePageDocument:
        """Retrieve one authenticated server-rendered profile details page."""
        ...


class PaginationFetcher(Protocol):
    async def fetch_page(
        self,
        request: SduiPaginationRequest,
        screen_id: str,
    ) -> ComoFlightDocument:
        """Retrieve one lazy SDUI pagination response."""
        ...


SKILLS_SCREEN_ID = "com.linkedin.sdui.flagshipnav.profile.ProfileSkillDetails"
EDUCATION_SCREEN_ID = "com.linkedin.sdui.flagshipnav.profile.ProfileEducationDetails"
PROJECTS_SCREEN_ID = "com.linkedin.sdui.flagshipnav.profile.ProfileProjectDetails"
MAX_SKILLS_PAGES = 20
MAX_EDUCATION_PAGES = 20
MAX_PROJECTS_PAGES = 20


class SsrLinkedInProfileClient:
    """Fetch and normalize the profile data eagerly embedded by LinkedIn."""

    def __init__(
        self,
        transport: ProfilePageFetcher,
        component_transport: ComponentFetcher | None = None,
        details_transport: ProfileDetailsFetcher | None = None,
        pagination_transport: PaginationFetcher | None = None,
    ) -> None:
        self._transport = transport
        self._component_transport = component_transport
        self._details_transport = details_transport
        self._pagination_transport = pagination_transport

    async def fetch_profile(self, request: ProfileRequest) -> LinkedInProfile:
        document = await self._transport.fetch_profile_page(request.public_identifier)
        try:
            profile = extract_profile_from_como(document.html, request.public_identifier)
            component_requests = extract_component_requests(document.html)
        except ComoFlightParseError as error:
            raise LinkedInInvalidResponseError from error

        if self._component_transport is None:
            return profile
        updates: dict[str, object] = {}
        for component_request in component_requests:
            component_id = component_request.component_id
            if not component_id.endswith(
                (
                    "profileCardsAboveActivity",
                    "profileCardsExperienceOnly",
                    "profileCardsBelowActivityPart1WithoutExp",
                    "profileCardsBelowActivityPart4",
                    "profileCardsBelowActivityPart7",
                )
            ):
                continue
            component = await self._component_transport.fetch_component(
                component_request
            )
            if component_id.endswith("profileCardsAboveActivity"):
                updates["about"] = extract_about_from_flight(component)
            elif component_id.endswith("profileCardsExperienceOnly"):
                preview_experiences = extract_experiences_from_flight(component)
                details_path = extract_profile_details_path(component, "experience")
                if (
                    details_path
                    == f"/in/{request.public_identifier}/details/experience/"
                    and self._details_transport is not None
                ):
                    updates["experiences"] = await self._fetch_all_experiences(
                        request.public_identifier,
                        preview_experiences,
                    )
                else:
                    updates["experiences"] = preview_experiences
            elif component_id.endswith("profileCardsBelowActivityPart1WithoutExp"):
                updates["education"] = extract_education_from_flight(component)
                updates["certifications"] = extract_certifications_from_flight(
                    component
                )
                projects_path = extract_profile_details_path(component, "projects")
                if (
                    projects_path
                    == f"/in/{request.public_identifier}/details/projects/"
                    and self._details_transport is not None
                    and self._pagination_transport is not None
                ):
                    updates["projects"] = await self._fetch_all_projects(
                        request.public_identifier
                    )
            elif component_id.endswith("profileCardsBelowActivityPart4"):
                updates["languages"] = extract_languages_from_flight(component)
            elif component_id.endswith("profileCardsBelowActivityPart7"):
                preview_skills = extract_skills_from_flight(component)
                details_path = extract_skills_details_path(component)
                expected_path = (
                    f"/in/{request.public_identifier}/details/skills/"
                )
                if (
                    details_path == expected_path
                    and self._details_transport is not None
                    and self._pagination_transport is not None
                ):
                    updates["skills"] = await self._fetch_all_skills(
                        request.public_identifier,
                        preview_skills,
                    )
                else:
                    updates["skills"] = preview_skills
        if self._details_transport is not None and not updates.get("education"):
            updates["education"] = await self._fetch_all_education(
                request.public_identifier
            )
        if (
            self._details_transport is not None
            and self._pagination_transport is not None
            and not updates.get("skills")
        ):
            updates["skills"] = await self._fetch_all_skills(
                request.public_identifier,
                [],
            )
        return profile.model_copy(update=updates)

    async def _fetch_all_education(
        self,
        public_identifier: str,
    ) -> list[Education]:
        assert self._details_transport is not None
        details_page = await self._details_transport.fetch_profile_details_page(
            public_identifier,
            "education",
        )
        try:
            details_document = parse_como_flight(details_page.html)
        except ComoFlightParseError as error:
            raise LinkedInInvalidResponseError from error
        inline_education = extract_education_from_flight(details_document)
        pagination_request = extract_education_pagination_request(details_document)
        if pagination_request is None or self._pagination_transport is None:
            return inline_education

        education: list[Education] = []
        seen_requests: set[str] = set()
        for _ in range(MAX_EDUCATION_PAGES):
            request_key = _pagination_request_key(pagination_request)
            if request_key in seen_requests:
                raise LinkedInInvalidResponseError
            seen_requests.add(request_key)
            page = await self._pagination_transport.fetch_page(
                pagination_request,
                EDUCATION_SCREEN_ID,
            )
            for item in extract_education_from_flight(page):
                if item not in education:
                    education.append(item)
            next_request = extract_education_pagination_request(page)
            if next_request is None:
                return education or inline_education
            pagination_request = next_request
        raise LinkedInInvalidResponseError

    async def _fetch_all_experiences(
        self,
        public_identifier: str,
        preview_experiences: list[Position],
    ) -> list[Position]:
        assert self._details_transport is not None
        details_page = await self._details_transport.fetch_profile_details_page(
            public_identifier,
            "experience",
        )
        try:
            details_document = parse_como_flight(details_page.html)
        except ComoFlightParseError as error:
            raise LinkedInInvalidResponseError from error
        experiences = extract_experiences_from_flight(details_document)
        return experiences or preview_experiences

    async def _fetch_all_projects(self, public_identifier: str) -> list[Project]:
        assert self._details_transport is not None
        assert self._pagination_transport is not None
        details_page = await self._details_transport.fetch_profile_details_page(
            public_identifier,
            "projects",
        )
        try:
            details_document = parse_como_flight(details_page.html)
        except ComoFlightParseError as error:
            raise LinkedInInvalidResponseError from error
        pagination_request = extract_projects_pagination_request(details_document)
        if pagination_request is None:
            return extract_projects_from_flight(details_document)

        projects: list[Project] = []
        seen_titles: set[str] = set()
        seen_requests: set[str] = set()
        for _ in range(MAX_PROJECTS_PAGES):
            request_key = _pagination_request_key(pagination_request)
            if request_key in seen_requests:
                raise LinkedInInvalidResponseError
            seen_requests.add(request_key)
            page = await self._pagination_transport.fetch_page(
                pagination_request,
                PROJECTS_SCREEN_ID,
            )
            for project in extract_projects_from_flight(page):
                if project.title not in seen_titles:
                    seen_titles.add(project.title)
                    projects.append(project)
            next_request = extract_projects_pagination_request(page)
            if next_request is None:
                return projects
            pagination_request = next_request
        raise LinkedInInvalidResponseError

    async def _fetch_all_skills(
        self,
        public_identifier: str,
        preview_skills: list[str],
    ) -> list[str]:
        assert self._details_transport is not None
        assert self._pagination_transport is not None

        details_page = await self._details_transport.fetch_profile_details_page(
            public_identifier,
            "skills",
        )
        try:
            details_document = parse_como_flight(details_page.html)
        except ComoFlightParseError as error:
            raise LinkedInInvalidResponseError from error
        pagination_request = extract_skills_pagination_request(details_document)
        if pagination_request is None:
            return extract_skills_from_flight(details_document) or preview_skills

        skills: list[str] = []
        seen_requests: set[str] = set()
        for _ in range(MAX_SKILLS_PAGES):
            request_key = _pagination_request_key(pagination_request)
            if request_key in seen_requests:
                raise LinkedInInvalidResponseError
            seen_requests.add(request_key)

            page = await self._pagination_transport.fetch_page(
                pagination_request,
                SKILLS_SCREEN_ID,
            )
            for skill in extract_skills_from_flight(page):
                if skill not in skills:
                    skills.append(skill)

            next_request = extract_skills_pagination_request(page)
            if next_request is None:
                return skills or preview_skills
            pagination_request = next_request

        raise LinkedInInvalidResponseError


def _pagination_request_key(request: SduiPaginationRequest) -> str:
    return json.dumps(
        request.raw_request,
        sort_keys=True,
        separators=(",", ":"),
    )
