"""Profile client backed by LinkedIn's authenticated SSR response."""

import asyncio
import json
from collections.abc import Coroutine
from typing import Any, Literal, Protocol

from tross_linkedin_api.clients.linkedin import ProfilePageDocument
from tross_linkedin_api.errors import LinkedInInvalidResponseError
from tross_linkedin_api.parsers.como import (
    ComoFlightDocument,
    ComoFlightParseError,
    SduiComponentRequest,
    SduiPaginationRequest,
    extract_about_from_flight,
    extract_causes_from_flight,
    extract_certifications_from_flight,
    extract_certifications_pagination_request,
    extract_component_requests,
    extract_courses_from_flight,
    extract_courses_pagination_request,
    extract_education_from_flight,
    extract_education_pagination_request,
    extract_experiences_from_flight,
    extract_honors_from_flight,
    extract_honors_pagination_request,
    extract_languages_from_flight,
    extract_organizations_from_flight,
    extract_organizations_pagination_request,
    extract_profile_details_path,
    extract_profile_from_como,
    extract_projects_from_flight,
    extract_projects_pagination_request,
    extract_publications_from_flight,
    extract_publications_pagination_request,
    extract_recommendations_from_flight,
    extract_recommendations_pagination_requests,
    extract_skills_details_path,
    extract_skills_from_flight,
    extract_skills_pagination_request,
    extract_test_scores_from_flight,
    extract_test_scores_pagination_request,
    extract_volunteer_experiences_from_flight,
    has_recommendations_section,
    parse_como_flight,
)
from tross_linkedin_api.schemas.profile import (
    Certification,
    Course,
    Education,
    Honor,
    LinkedInProfile,
    Organization,
    Position,
    ProfileRequest,
    Project,
    Publication,
    Recommendation,
    TestScore,
    VolunteerExperience,
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
CERTIFICATIONS_SCREEN_ID = (
    "com.linkedin.sdui.flagshipnav.profile.ProfileCertificationDetails"
)
COURSES_SCREEN_ID = "com.linkedin.sdui.flagshipnav.profile.ProfileCourseDetails"
HONORS_SCREEN_ID = "com.linkedin.sdui.flagshipnav.profile.ProfileHonorDetails"
ORGANIZATIONS_SCREEN_ID = (
    "com.linkedin.sdui.flagshipnav.profile.ProfileOrganizationDetails"
)
PROJECTS_SCREEN_ID = "com.linkedin.sdui.flagshipnav.profile.ProfileProjectDetails"
PUBLICATIONS_SCREEN_ID = (
    "com.linkedin.sdui.flagshipnav.profile.ProfilePublicationDetails"
)
RECOMMENDATIONS_SCREEN_ID = (
    "com.linkedin.sdui.flagshipnav.profile.ProfileRecommendationDetails"
)
TEST_SCORES_SCREEN_ID = (
    "com.linkedin.sdui.flagshipnav.profile.ProfileTestScoreDetails"
)
MAX_SKILLS_PAGES = 20
MAX_EDUCATION_PAGES = 20
MAX_CERTIFICATION_PAGES = 20
MAX_COURSE_PAGES = 20
MAX_HONORS_PAGES = 20
MAX_ORGANIZATION_PAGES = 20
MAX_PROJECTS_PAGES = 20
MAX_PUBLICATION_PAGES = 20
MAX_RECOMMENDATION_PAGES = 20
MAX_TEST_SCORE_PAGES = 20
MAX_CONCURRENT_LINKEDIN_REQUESTS = 3


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
        self._request_semaphore = asyncio.Semaphore(MAX_CONCURRENT_LINKEDIN_REQUESTS)

    async def fetch_profile(self, request: ProfileRequest) -> LinkedInProfile:
        document = await self._transport.fetch_profile_page(request.public_identifier)
        try:
            profile = extract_profile_from_como(document.html, request.public_identifier)
            component_requests = extract_component_requests(document.html)
        except ComoFlightParseError as error:
            raise LinkedInInvalidResponseError from error

        if self._component_transport is None:
            return profile
        supported_requests = [
            component_request
            for component_request in component_requests
            if component_request.component_id.endswith(
                (
                    "profileCardsAboveActivity",
                    "profileCardsExperienceOnly",
                    "profileCardsBelowActivityPart1WithoutExp",
                    "profileCardsBelowActivityPart2",
                    "profileCardsBelowActivityPart3",
                    "profileCardsBelowActivityPart4",
                    "profileCardsBelowActivityPart6",
                    "profileCardsBelowActivityPart7",
                )
            )
        ]
        async with asyncio.TaskGroup() as task_group:
            component_tasks = [
                task_group.create_task(
                    self._fetch_component_updates(request, component_request)
                )
                for component_request in supported_requests
            ]
        component_updates = [task.result() for task in component_tasks]
        updates = {
            key: value
            for component_update in component_updates
            for key, value in component_update.items()
        }

        fallback_fetches: dict[str, Coroutine[Any, Any, object]] = {}
        if self._details_transport is not None and not updates.get("education"):
            fallback_fetches["education"] = self._fetch_all_education(
                request.public_identifier
            )
        if (
            self._details_transport is not None
            and self._pagination_transport is not None
            and not updates.get("skills")
        ):
            fallback_fetches["skills"] = self._fetch_all_skills(
                request.public_identifier,
                [],
            )
        updates.update(await _gather_named(fallback_fetches))
        return profile.model_copy(update=updates)

    async def _fetch_component_updates(
        self,
        request: ProfileRequest,
        component_request: SduiComponentRequest,
    ) -> dict[str, object]:
        component = await self._fetch_component(component_request)
        component_id = component_request.component_id
        if component_id.endswith("profileCardsAboveActivity"):
            return {"about": extract_about_from_flight(component)}
        if component_id.endswith("profileCardsExperienceOnly"):
            preview_experiences = extract_experiences_from_flight(component)
            details_path = extract_profile_details_path(component, "experience")
            if (
                details_path
                == f"/in/{request.public_identifier}/details/experience/"
                and self._details_transport is not None
            ):
                return {
                    "experiences": await self._fetch_all_experiences(
                        request.public_identifier,
                        preview_experiences,
                    )
                }
            return {"experiences": preview_experiences}
        if component_id.endswith("profileCardsBelowActivityPart1WithoutExp"):
            return await self._fetch_part_one_updates(request, component)
        if component_id.endswith("profileCardsBelowActivityPart2"):
            if (
                has_recommendations_section(component)
                and self._details_transport is not None
                and self._pagination_transport is not None
            ):
                return {
                    "recommendations": await self._fetch_all_recommendations(
                        request.public_identifier
                    )
                }
            return {}
        if component_id.endswith("profileCardsBelowActivityPart3"):
            return await self._fetch_part_three_updates(request, component)
        if component_id.endswith("profileCardsBelowActivityPart4"):
            updates: dict[str, object] = {
                "languages": extract_languages_from_flight(component)
            }
            preview_organizations = extract_organizations_from_flight(component)
            details_path = extract_profile_details_path(component, "organizations")
            if (
                details_path
                == f"/in/{request.public_identifier}/details/organizations/"
                and self._details_transport is not None
                and self._pagination_transport is not None
            ):
                updates["organizations"] = await self._fetch_all_organizations(
                    request.public_identifier,
                    preview_organizations,
                )
            else:
                updates["organizations"] = preview_organizations
            return updates
        if component_id.endswith("profileCardsBelowActivityPart6"):
            return {"causes": extract_causes_from_flight(component)}
        if component_id.endswith("profileCardsBelowActivityPart7"):
            preview_skills = extract_skills_from_flight(component)
            details_path = extract_skills_details_path(component)
            if (
                details_path
                == f"/in/{request.public_identifier}/details/skills/"
                and self._details_transport is not None
                and self._pagination_transport is not None
            ):
                return {
                    "skills": await self._fetch_all_skills(
                        request.public_identifier,
                        preview_skills,
                    )
                }
            return {"skills": preview_skills}
        return {}

    async def _fetch_part_one_updates(
        self,
        request: ProfileRequest,
        component: ComoFlightDocument,
    ) -> dict[str, object]:
        updates: dict[str, object] = {
            "education": extract_education_from_flight(component)
        }
        detail_fetches: dict[str, Coroutine[Any, Any, object]] = {}

        preview_volunteering = extract_volunteer_experiences_from_flight(component)
        volunteering_path = extract_profile_details_path(
            component,
            "volunteering-experiences",
        )
        if (
            volunteering_path
            == (
                f"/in/{request.public_identifier}/details/"
                "volunteering-experiences/"
            )
            and self._details_transport is not None
        ):
            detail_fetches["volunteer_experiences"] = (
                self._fetch_all_volunteer_experiences(
                    request.public_identifier,
                    preview_volunteering,
                )
            )
        else:
            updates["volunteer_experiences"] = preview_volunteering

        preview_certifications = extract_certifications_from_flight(component)
        certifications_path = extract_profile_details_path(component, "certifications")
        if (
            certifications_path
            == f"/in/{request.public_identifier}/details/certifications/"
            and self._details_transport is not None
            and self._pagination_transport is not None
        ):
            detail_fetches["certifications"] = self._fetch_all_certifications(
                request.public_identifier,
                preview_certifications,
            )
        else:
            updates["certifications"] = preview_certifications

        preview_projects = extract_projects_from_flight(component)
        projects_path = extract_profile_details_path(component, "projects")
        if (
            projects_path == f"/in/{request.public_identifier}/details/projects/"
            and self._details_transport is not None
            and self._pagination_transport is not None
        ):
            detail_fetches["projects"] = self._fetch_all_projects(
                request.public_identifier
            )
        else:
            updates["projects"] = preview_projects

        updates.update(await _gather_named(detail_fetches))
        return updates

    async def _fetch_part_three_updates(
        self,
        request: ProfileRequest,
        component: ComoFlightDocument,
    ) -> dict[str, object]:
        updates: dict[str, object] = {}
        detail_fetches: dict[str, Coroutine[Any, Any, object]] = {}
        can_fetch_details = (
            self._details_transport is not None
            and self._pagination_transport is not None
        )

        preview_honors = extract_honors_from_flight(component)
        if can_fetch_details and extract_profile_details_path(
            component, "honors"
        ) == f"/in/{request.public_identifier}/details/honors/":
            detail_fetches["honors"] = self._fetch_all_honors(
                request.public_identifier,
                preview_honors,
            )
        else:
            updates["honors"] = preview_honors

        preview_courses = extract_courses_from_flight(component)
        if can_fetch_details and extract_profile_details_path(
            component, "courses"
        ) == f"/in/{request.public_identifier}/details/courses/":
            detail_fetches["courses"] = self._fetch_all_courses(
                request.public_identifier,
                preview_courses,
            )
        else:
            updates["courses"] = preview_courses

        preview_publications = extract_publications_from_flight(component)
        if can_fetch_details and extract_profile_details_path(
            component, "publications"
        ) == f"/in/{request.public_identifier}/details/publications/":
            detail_fetches["publications"] = self._fetch_all_publications(
                request.public_identifier,
                preview_publications,
            )
        else:
            updates["publications"] = preview_publications

        preview_test_scores = extract_test_scores_from_flight(component)
        if can_fetch_details and extract_profile_details_path(
            component, "test-scores"
        ) == f"/in/{request.public_identifier}/details/test-scores/":
            detail_fetches["test_scores"] = self._fetch_all_test_scores(
                request.public_identifier,
                preview_test_scores,
            )
        else:
            updates["test_scores"] = preview_test_scores

        updates.update(await _gather_named(detail_fetches))
        return updates

    async def _fetch_component(
        self,
        request: SduiComponentRequest,
    ) -> ComoFlightDocument:
        assert self._component_transport is not None
        async with self._request_semaphore:
            return await self._component_transport.fetch_component(request)

    async def _fetch_details_page(
        self,
        public_identifier: str,
        section: str,
    ) -> ProfilePageDocument:
        assert self._details_transport is not None
        async with self._request_semaphore:
            return await self._details_transport.fetch_profile_details_page(
                public_identifier,
                section,
            )

    async def _fetch_pagination_page(
        self,
        request: SduiPaginationRequest,
        screen_id: str,
    ) -> ComoFlightDocument:
        assert self._pagination_transport is not None
        async with self._request_semaphore:
            return await self._pagination_transport.fetch_page(request, screen_id)

    async def _fetch_all_education(
        self,
        public_identifier: str,
    ) -> list[Education]:
        assert self._details_transport is not None
        details_page = await self._fetch_details_page(
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
            page = await self._fetch_pagination_page(
                pagination_request,
                EDUCATION_SCREEN_ID,
            )
            page_items = extract_education_from_flight(page)
            for item in page_items:
                if item not in education:
                    education.append(item)
            next_request = extract_education_pagination_request(page)
            if next_request is None:
                next_request = _advance_full_page_request(
                    pagination_request,
                    len(page_items),
                )
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
        details_page = await self._fetch_details_page(
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
        details_page = await self._fetch_details_page(
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
            page = await self._fetch_pagination_page(
                pagination_request,
                PROJECTS_SCREEN_ID,
            )
            page_items = extract_projects_from_flight(page)
            for project in page_items:
                if project.title not in seen_titles:
                    seen_titles.add(project.title)
                    projects.append(project)
            next_request = extract_projects_pagination_request(page)
            if next_request is None:
                next_request = _advance_full_page_request(
                    pagination_request,
                    len(page_items),
                )
            if next_request is None:
                return projects
            pagination_request = next_request
        raise LinkedInInvalidResponseError

    async def _fetch_all_certifications(
        self,
        public_identifier: str,
        preview_certifications: list[Certification],
    ) -> list[Certification]:
        assert self._pagination_transport is not None
        details_document = await self._fetch_details_document(
            public_identifier,
            "certifications",
        )
        inline_items = extract_certifications_from_flight(details_document)
        pagination_request = extract_certifications_pagination_request(
            details_document
        )
        if pagination_request is None:
            return inline_items or preview_certifications

        certifications: list[Certification] = []
        seen_requests: set[str] = set()
        for _ in range(MAX_CERTIFICATION_PAGES):
            request_key = _pagination_request_key(pagination_request)
            if request_key in seen_requests:
                raise LinkedInInvalidResponseError
            seen_requests.add(request_key)
            page = await self._fetch_pagination_page(
                pagination_request,
                CERTIFICATIONS_SCREEN_ID,
            )
            page_items = extract_certifications_from_flight(page)
            for certification in page_items:
                if certification not in certifications:
                    certifications.append(certification)
            next_request = extract_certifications_pagination_request(page)
            if next_request is None:
                next_request = _advance_full_page_request(
                    pagination_request,
                    len(page_items),
                )
            if next_request is None:
                return certifications or inline_items or preview_certifications
            pagination_request = next_request
        raise LinkedInInvalidResponseError

    async def _fetch_all_courses(
        self,
        public_identifier: str,
        preview_courses: list[Course],
    ) -> list[Course]:
        assert self._pagination_transport is not None
        details_document = await self._fetch_details_document(
            public_identifier,
            "courses",
        )
        inline_courses = extract_courses_from_flight(details_document)
        pagination_request = extract_courses_pagination_request(details_document)
        if pagination_request is None:
            return inline_courses or preview_courses

        courses: list[Course] = []
        seen_requests: set[str] = set()
        for _ in range(MAX_COURSE_PAGES):
            request_key = _pagination_request_key(pagination_request)
            if request_key in seen_requests:
                raise LinkedInInvalidResponseError
            seen_requests.add(request_key)
            page = await self._fetch_pagination_page(
                pagination_request,
                COURSES_SCREEN_ID,
            )
            page_items = extract_courses_from_flight(page)
            for course in page_items:
                if course not in courses:
                    courses.append(course)
            next_request = extract_courses_pagination_request(page)
            if next_request is None:
                next_request = _advance_full_page_request(
                    pagination_request,
                    len(page_items),
                )
            if next_request is None:
                return courses or inline_courses or preview_courses
            pagination_request = next_request
        raise LinkedInInvalidResponseError

    async def _fetch_all_publications(
        self,
        public_identifier: str,
        preview_publications: list[Publication],
    ) -> list[Publication]:
        assert self._details_transport is not None
        assert self._pagination_transport is not None
        details_document = await self._fetch_details_document(
            public_identifier,
            "publications",
        )
        inline_publications = extract_publications_from_flight(details_document)
        pagination_request = extract_publications_pagination_request(details_document)
        if pagination_request is None:
            return inline_publications or preview_publications

        publications: list[Publication] = []
        seen_requests: set[str] = set()
        for _ in range(MAX_PUBLICATION_PAGES):
            request_key = _pagination_request_key(pagination_request)
            if request_key in seen_requests:
                raise LinkedInInvalidResponseError
            seen_requests.add(request_key)
            page = await self._fetch_pagination_page(
                pagination_request,
                PUBLICATIONS_SCREEN_ID,
            )
            page_items = extract_publications_from_flight(page)
            for publication in page_items:
                if publication not in publications:
                    publications.append(publication)
            next_request = extract_publications_pagination_request(page)
            if next_request is None:
                next_request = _advance_full_page_request(
                    pagination_request,
                    len(page_items),
                )
            if next_request is None:
                return publications or inline_publications or preview_publications
            pagination_request = next_request
        raise LinkedInInvalidResponseError

    async def _fetch_all_honors(
        self,
        public_identifier: str,
        preview_honors: list[Honor],
    ) -> list[Honor]:
        assert self._pagination_transport is not None
        details_document = await self._fetch_details_document(
            public_identifier,
            "honors",
        )
        inline_honors = extract_honors_from_flight(details_document)
        pagination_request = extract_honors_pagination_request(details_document)
        if pagination_request is None:
            return inline_honors or preview_honors

        honors: list[Honor] = []
        seen_requests: set[str] = set()
        for _ in range(MAX_HONORS_PAGES):
            request_key = _pagination_request_key(pagination_request)
            if request_key in seen_requests:
                raise LinkedInInvalidResponseError
            seen_requests.add(request_key)
            page = await self._fetch_pagination_page(
                pagination_request,
                HONORS_SCREEN_ID,
            )
            page_items = extract_honors_from_flight(page)
            for honor in page_items:
                if honor not in honors:
                    honors.append(honor)
            next_request = extract_honors_pagination_request(page)
            if next_request is None:
                next_request = _advance_full_page_request(
                    pagination_request,
                    len(page_items),
                )
            if next_request is None:
                return honors or inline_honors or preview_honors
            pagination_request = next_request
        raise LinkedInInvalidResponseError

    async def _fetch_all_volunteer_experiences(
        self,
        public_identifier: str,
        preview_experiences: list[VolunteerExperience],
    ) -> list[VolunteerExperience]:
        details_document = await self._fetch_details_document(
            public_identifier,
            "volunteering-experiences",
        )
        return (
            extract_volunteer_experiences_from_flight(details_document)
            or preview_experiences
        )

    async def _fetch_all_organizations(
        self,
        public_identifier: str,
        preview_organizations: list[Organization],
    ) -> list[Organization]:
        assert self._pagination_transport is not None
        details_document = await self._fetch_details_document(
            public_identifier,
            "organizations",
        )
        inline_organizations = extract_organizations_from_flight(details_document)
        pagination_request = extract_organizations_pagination_request(details_document)
        if pagination_request is None:
            return inline_organizations or preview_organizations

        organizations: list[Organization] = []
        seen_requests: set[str] = set()
        for _ in range(MAX_ORGANIZATION_PAGES):
            request_key = _pagination_request_key(pagination_request)
            if request_key in seen_requests:
                raise LinkedInInvalidResponseError
            seen_requests.add(request_key)
            page = await self._fetch_pagination_page(
                pagination_request,
                ORGANIZATIONS_SCREEN_ID,
            )
            page_items = extract_organizations_from_flight(page)
            for organization in page_items:
                if organization not in organizations:
                    organizations.append(organization)
            next_request = extract_organizations_pagination_request(page)
            if next_request is None:
                next_request = _advance_full_page_request(
                    pagination_request,
                    len(page_items),
                )
            if next_request is None:
                return organizations or inline_organizations or preview_organizations
            pagination_request = next_request
        raise LinkedInInvalidResponseError

    async def _fetch_all_test_scores(
        self,
        public_identifier: str,
        preview_scores: list[TestScore],
    ) -> list[TestScore]:
        assert self._details_transport is not None
        assert self._pagination_transport is not None
        details_document = await self._fetch_details_document(
            public_identifier,
            "test-scores",
        )
        inline_scores = extract_test_scores_from_flight(details_document)
        pagination_request = extract_test_scores_pagination_request(details_document)
        if pagination_request is None:
            return inline_scores or preview_scores

        scores: list[TestScore] = []
        seen_requests: set[str] = set()
        for _ in range(MAX_TEST_SCORE_PAGES):
            request_key = _pagination_request_key(pagination_request)
            if request_key in seen_requests:
                raise LinkedInInvalidResponseError
            seen_requests.add(request_key)
            page = await self._fetch_pagination_page(
                pagination_request,
                TEST_SCORES_SCREEN_ID,
            )
            page_items = extract_test_scores_from_flight(page)
            for score in page_items:
                if score not in scores:
                    scores.append(score)
            next_request = extract_test_scores_pagination_request(page)
            if next_request is None:
                next_request = _advance_full_page_request(
                    pagination_request,
                    len(page_items),
                )
            if next_request is None:
                return scores or inline_scores or preview_scores
            pagination_request = next_request
        raise LinkedInInvalidResponseError

    async def _fetch_all_recommendations(
        self,
        public_identifier: str,
    ) -> list[Recommendation]:
        assert self._details_transport is not None
        assert self._pagination_transport is not None
        details_document = await self._fetch_details_document(
            public_identifier,
            "recommendations",
        )
        recommendations: list[Recommendation] = []
        for pagination_request in extract_recommendations_pagination_requests(
            details_document
        ):
            payload = pagination_request.payload()
            assert payload is not None
            raw_type = payload.get("type")
            if not isinstance(raw_type, str) or raw_type not in {"Received", "Given"}:
                continue
            recommendation_type: Literal["received", "given"] = (
                "received" if raw_type == "Received" else "given"
            )
            seen_requests: set[str] = set()
            for _ in range(MAX_RECOMMENDATION_PAGES):
                request_key = _pagination_request_key(pagination_request)
                if request_key in seen_requests:
                    raise LinkedInInvalidResponseError
                seen_requests.add(request_key)
                page = await self._fetch_pagination_page(
                    pagination_request,
                    RECOMMENDATIONS_SCREEN_ID,
                )
                page_items = extract_recommendations_from_flight(
                    page,
                    recommendation_type,
                )
                for recommendation in page_items:
                    if recommendation not in recommendations:
                        recommendations.append(recommendation)
                next_requests = extract_recommendations_pagination_requests(page)
                next_request = next(
                    (
                        request
                        for request in next_requests
                        if _pagination_request_type(request) == raw_type
                    ),
                    None,
                )
                if next_request is None:
                    next_request = _advance_full_page_request(
                        pagination_request,
                        len(page_items),
                    )
                if next_request is None:
                    break
                pagination_request = next_request
            else:
                raise LinkedInInvalidResponseError
        return recommendations

    async def _fetch_details_document(
        self,
        public_identifier: str,
        section: str,
    ) -> ComoFlightDocument:
        assert self._details_transport is not None
        details_page = await self._fetch_details_page(
            public_identifier,
            section,
        )
        try:
            return parse_como_flight(details_page.html)
        except ComoFlightParseError as error:
            raise LinkedInInvalidResponseError from error

    async def _fetch_all_skills(
        self,
        public_identifier: str,
        preview_skills: list[str],
    ) -> list[str]:
        assert self._details_transport is not None
        assert self._pagination_transport is not None

        details_page = await self._fetch_details_page(
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

            page = await self._fetch_pagination_page(
                pagination_request,
                SKILLS_SCREEN_ID,
            )
            page_items = extract_skills_from_flight(page)
            for skill in page_items:
                if skill not in skills:
                    skills.append(skill)

            next_request = extract_skills_pagination_request(page)
            if next_request is None:
                next_request = _advance_full_page_request(
                    pagination_request,
                    len(page_items),
                )
            if next_request is None:
                return skills or preview_skills
            pagination_request = next_request

        raise LinkedInInvalidResponseError


async def _gather_named(
    fetches: dict[str, Coroutine[Any, Any, object]],
) -> dict[str, object]:
    """Await independent section fetches while preserving their output names."""

    if not fetches:
        return {}
    async with asyncio.TaskGroup() as task_group:
        tasks: dict[str, asyncio.Task[object]] = {
            name: task_group.create_task(fetch)
            for name, fetch in fetches.items()
        }
    return {name: task.result() for name, task in tasks.items()}


def _pagination_request_key(request: SduiPaginationRequest) -> str:
    return json.dumps(
        request.raw_request,
        sort_keys=True,
        separators=(",", ":"),
    )


def _pagination_request_type(request: SduiPaginationRequest) -> str | None:
    payload = request.payload()
    value = payload.get("type") if payload is not None else None
    return value if isinstance(value, str) else None


def _advance_full_page_request(
    request: SduiPaginationRequest,
    returned_items: int,
) -> SduiPaginationRequest | None:
    """Advance pagers that omit a next request when the current page is full."""

    payload = request.payload()
    if payload is None:
        return None
    start = payload.get("start")
    count = payload.get("count")
    if type(start) is not int or type(count) is not int or count <= 0:
        return None
    if returned_items < count:
        return None
    next_arguments = {
        **request.requested_arguments,
        "payload": {**payload, "start": start + count},
    }
    return SduiPaginationRequest(
        pager_id=request.pager_id,
        requested_arguments=next_arguments,
        raw_request={
            **request.raw_request,
            "requestedArguments": next_arguments,
        },
    )
