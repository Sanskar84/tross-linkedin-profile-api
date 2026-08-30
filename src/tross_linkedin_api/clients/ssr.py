"""Profile client backed by LinkedIn's authenticated SSR response."""

import json
from typing import Literal, Protocol

from tross_linkedin_api.clients.linkedin import ProfilePageDocument
from tross_linkedin_api.errors import LinkedInInvalidResponseError
from tross_linkedin_api.parsers.como import (
    ComoFlightDocument,
    ComoFlightParseError,
    SduiComponentRequest,
    SduiPaginationRequest,
    extract_about_from_flight,
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
    has_recommendations_section,
    parse_como_flight,
)
from tross_linkedin_api.schemas.profile import (
    Certification,
    Course,
    Education,
    Honor,
    LinkedInProfile,
    Position,
    ProfileRequest,
    Project,
    Publication,
    Recommendation,
    TestScore,
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
MAX_PROJECTS_PAGES = 20
MAX_PUBLICATION_PAGES = 20
MAX_RECOMMENDATION_PAGES = 20
MAX_TEST_SCORE_PAGES = 20


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
                    "profileCardsBelowActivityPart2",
                    "profileCardsBelowActivityPart3",
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
                preview_certifications = extract_certifications_from_flight(component)
                certifications_path = extract_profile_details_path(
                    component,
                    "certifications",
                )
                if (
                    certifications_path
                    == f"/in/{request.public_identifier}/details/certifications/"
                    and self._details_transport is not None
                    and self._pagination_transport is not None
                ):
                    updates["certifications"] = await self._fetch_all_certifications(
                        request.public_identifier,
                        preview_certifications,
                    )
                else:
                    updates["certifications"] = preview_certifications
                preview_projects = extract_projects_from_flight(component)
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
                else:
                    updates["projects"] = preview_projects
            elif component_id.endswith("profileCardsBelowActivityPart2"):
                if (
                    has_recommendations_section(component)
                    and self._details_transport is not None
                    and self._pagination_transport is not None
                ):
                    updates["recommendations"] = (
                        await self._fetch_all_recommendations(
                            request.public_identifier
                        )
                    )
            elif component_id.endswith("profileCardsBelowActivityPart3"):
                preview_honors = extract_honors_from_flight(component)
                honors_path = extract_profile_details_path(component, "honors")
                if (
                    honors_path
                    == f"/in/{request.public_identifier}/details/honors/"
                    and self._details_transport is not None
                    and self._pagination_transport is not None
                ):
                    updates["honors"] = await self._fetch_all_honors(
                        request.public_identifier,
                        preview_honors,
                    )
                else:
                    updates["honors"] = preview_honors

                preview_courses = extract_courses_from_flight(component)
                courses_path = extract_profile_details_path(component, "courses")
                if (
                    courses_path
                    == f"/in/{request.public_identifier}/details/courses/"
                    and self._details_transport is not None
                    and self._pagination_transport is not None
                ):
                    updates["courses"] = await self._fetch_all_courses(
                        request.public_identifier,
                        preview_courses,
                    )
                else:
                    updates["courses"] = preview_courses

                preview_publications = extract_publications_from_flight(component)
                publications_path = extract_profile_details_path(
                    component,
                    "publications",
                )
                if (
                    publications_path
                    == f"/in/{request.public_identifier}/details/publications/"
                    and self._details_transport is not None
                    and self._pagination_transport is not None
                ):
                    updates["publications"] = await self._fetch_all_publications(
                        request.public_identifier,
                        preview_publications,
                    )
                else:
                    updates["publications"] = preview_publications

                preview_test_scores = extract_test_scores_from_flight(component)
                test_scores_path = extract_profile_details_path(
                    component,
                    "test-scores",
                )
                if (
                    test_scores_path
                    == f"/in/{request.public_identifier}/details/test-scores/"
                    and self._details_transport is not None
                    and self._pagination_transport is not None
                ):
                    updates["test_scores"] = await self._fetch_all_test_scores(
                        request.public_identifier,
                        preview_test_scores,
                    )
                else:
                    updates["test_scores"] = preview_test_scores
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
            page = await self._pagination_transport.fetch_page(
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
            page = await self._pagination_transport.fetch_page(
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
            page = await self._pagination_transport.fetch_page(
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
            page = await self._pagination_transport.fetch_page(
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
            page = await self._pagination_transport.fetch_page(
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
                page = await self._pagination_transport.fetch_page(
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
        details_page = await self._details_transport.fetch_profile_details_page(
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
