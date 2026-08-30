"""Static parser for LinkedIn's embedded COMO React Flight hydration stream."""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from tross_linkedin_api.schemas.profile import (
    Certification,
    DateParts,
    Education,
    Language,
    LinkedInProfile,
    Position,
    ProfileImage,
    Project,
)

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]

COMO_REHYDRATION_MARKER = "window.__como_rehydration__"
FLIGHT_RECORD_PATTERN = re.compile(r"^([0-9a-f]+):(.*)$", re.IGNORECASE)
FLIGHT_REFERENCE_PATTERN = re.compile(r"^\$L?([0-9a-f]+)$", re.IGNORECASE)
TOP_CARD_IDENTIFIER = "com.linkedin.sdui.impl.profile.components.topCard"
CONTACT_INFO_PATH_SUFFIX = "/overlay/contact-info/"
ASYNC_COMPONENT_REQUEST_TYPE = "proto.sdui.actions.core.AsyncComponentRequest"
PAGINATION_REQUEST_TYPE = "proto.sdui.actions.requests.PaginationRequest"
SKILLS_PAGER_ID = "com.linkedin.sdui.pagers.profile.details.skills"
EDUCATION_PAGER_ID = "com.linkedin.sdui.pagers.profile.details.education"
UUID_COMPONENT_KEY_PATTERN = re.compile(
    r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$",
    re.IGNORECASE,
)
PROJECTS_PAGER_ID = "com.linkedin.sdui.pagers.profile.details.projects"
SKILLS_ALL_FILTER = "ProfileSkillCategory_ALL"
PROFILE_DETAILS_SECTIONS = frozenset(
    {"education", "experience", "projects", "skills"}
)
EXPERIENCE_DATE_PATTERN = re.compile(
    r"^(?:(?P<start_month>[A-Z][a-z]{2}) )?(?P<start_year>\d{4}) [–-] "
    r"(?:(?:(?P<end_month>[A-Z][a-z]{2}) )?(?P<end_year>\d{4})|Present)"
)
EXPERIENCE_SINGLE_DATE_PATTERN = re.compile(
    r"^(?:(?P<start_month>[A-Z][a-z]{2}) )?(?P<start_year>\d{4})"
    r" · 1 (?:mo|yr)\b"
)
EDUCATION_DATE_PATTERN = re.compile(
    r"^(?:(?P<start_month>[A-Z][a-z]{2}) )?(?P<start_year>\d{4}) "
    r"[–-] (?:(?P<end_month>[A-Z][a-z]{2}) )?(?P<end_year>\d{4})"
)
CERTIFICATION_DATE_PATTERN = re.compile(
    r"^Issued (?:(?P<start_month>[A-Z][a-z]{2}) )?(?P<start_year>\d{4})"
    r"(?: · Expires (?:(?P<end_month>[A-Z][a-z]{2}) )?"
    r"(?P<end_year>\d{4}))?"
)
DEGREE_TRAILING_QUALIFIERS = {
    "cum laude",
    "distinction",
    "high distinction",
    "hons",
    "honors",
    "honours",
    "magna cum laude",
    "summa cum laude",
    "with honors",
    "with honours",
}
MONTH_NUMBERS = {
    month: number
    for number, month in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}


class ComoFlightParseError(ValueError):
    """Raised when an embedded COMO hydration stream cannot be decoded."""


@dataclass(frozen=True)
class ComoFlightDocument:
    """Decoded React Flight records with safe reference traversal helpers."""

    records: dict[str, JSONValue]
    opaque_record_ids: frozenset[str] = field(default_factory=frozenset)

    @property
    def record_count(self) -> int:
        return len(self.records)

    def resolve_reference(self, reference: str) -> JSONValue:
        match = FLIGHT_REFERENCE_PATTERN.fullmatch(reference)
        if match is None:
            raise KeyError(reference)
        return self.records[match.group(1).lower()]

    def reachable_record_ids(self, root_id: str) -> set[str]:
        return set(self.reachable_record_ids_in_order(root_id))

    def reachable_record_ids_in_order(self, root_id: str) -> list[str]:
        """Return reachable records in their first depth-first reference order."""

        if root_id not in self.records:
            raise KeyError(root_id)

        visited: set[str] = set()
        ordered: list[str] = []

        def visit(record_id: str) -> None:
            if record_id in visited:
                return
            visited.add(record_id)
            ordered.append(record_id)
            for reference_id in _reference_ids(self.records[record_id]):
                if reference_id in self.records:
                    visit(reference_id)

        visit(root_id)
        return ordered


@dataclass(frozen=True)
class SduiComponentRequest:
    """A lazy component request descriptor embedded in the profile page."""

    component_id: str
    requested_arguments: dict[str, JSONValue]

    def client_arguments(self) -> dict[str, JSONValue]:
        return {
            "payload": self.requested_arguments.get("payload"),
            "states": [],
            "requestMetadata": self.requested_arguments.get("requestMetadata"),
            "screenId": "",
            "knownTemplateIds": [],
        }


@dataclass(frozen=True)
class SduiPaginationRequest:
    """A pagination request descriptor embedded in a React Flight document."""

    pager_id: str
    requested_arguments: dict[str, JSONValue]
    raw_request: dict[str, JSONValue]

    def client_arguments(self, screen_id: str) -> dict[str, JSONValue]:
        return {
            **self.requested_arguments,
            "states": [],
            "screenId": screen_id,
        }


def parse_como_flight(html: str) -> ComoFlightDocument:
    """Decode the JSON chunk array and every Flight record without executing JS."""

    marker_index = html.find(COMO_REHYDRATION_MARKER)
    if marker_index < 0:
        raise ComoFlightParseError("COMO hydration marker is missing")

    assignment_index = html.find("=", marker_index + len(COMO_REHYDRATION_MARKER))
    if assignment_index < 0:
        raise ComoFlightParseError("COMO hydration assignment is missing")

    try:
        chunks, _ = json.JSONDecoder().raw_decode(html[assignment_index + 1 :].lstrip())
    except json.JSONDecodeError as error:
        raise ComoFlightParseError("COMO hydration chunks are invalid JSON") from error

    if not isinstance(chunks, list) or not chunks or not all(
        isinstance(chunk, str) for chunk in chunks
    ):
        raise ComoFlightParseError("COMO hydration must be a non-empty string array")

    return parse_flight_stream("".join(chunks))


def parse_flight_stream(stream: str) -> ComoFlightDocument:
    """Decode a raw React Flight response into addressable records."""

    records: dict[str, JSONValue] = {}
    opaque_record_ids: set[str] = set()
    for raw_line in stream.split("\n"):
        line = raw_line.removesuffix("\r")
        if not line:
            continue
        match = FLIGHT_RECORD_PATTERN.fullmatch(line)
        if match is None:
            raise ComoFlightParseError("Malformed React Flight record")
        record_id, payload = match.groups()
        record_id = record_id.lower()
        if record_id in records:
            raise ComoFlightParseError("Duplicate React Flight record identifier")
        if payload.startswith("I"):
            payload = payload[1:]
        try:
            records[record_id] = json.loads(payload)
        except json.JSONDecodeError:
            records[record_id] = payload
            opaque_record_ids.add(record_id)

    if not records:
        raise ComoFlightParseError("COMO hydration contains no React Flight records")
    return ComoFlightDocument(records, frozenset(opaque_record_ids))


def extract_component_requests(html: str) -> list[SduiComponentRequest]:
    """Return each lazy SDUI component request embedded in profile hydration."""

    document = parse_como_flight(html)
    requests: list[SduiComponentRequest] = []
    seen_component_ids: set[str] = set()
    for root in document.records.values():
        for value in _walk(root):
            if not isinstance(value, dict) or value.get("$type") != ASYNC_COMPONENT_REQUEST_TYPE:
                continue
            component_id = value.get("newComponentId")
            arguments = value.get("requestedArguments")
            if (
                not isinstance(component_id, str)
                or not isinstance(arguments, dict)
                or component_id in seen_component_ids
            ):
                continue
            seen_component_ids.add(component_id)
            requests.append(SduiComponentRequest(component_id, arguments))
    return requests


def extract_skills_details_path(document: ComoFlightDocument) -> str | None:
    """Return the validated direct URL for the complete skills view, if present."""

    return extract_profile_details_path(document, "skills")


def extract_profile_details_path(
    document: ComoFlightDocument,
    section: str,
) -> str | None:
    """Return a validated direct profile-details path for a known section."""

    if section not in PROFILE_DETAILS_SECTIONS:
        raise ValueError("Invalid LinkedIn profile details section")
    pattern = re.compile(
        rf"^/in/[A-Za-z0-9._~-]+/details/{re.escape(section)}/$"
    )

    for root in document.records.values():
        for value in _walk(root):
            if isinstance(value, str) and pattern.fullmatch(value):
                return value
    return None


def extract_skills_pagination_request(
    document: ComoFlightDocument,
) -> SduiPaginationRequest | None:
    """Return the all-skills pager request embedded in a details/page response."""

    for root in document.records.values():
        for value in _walk(root):
            if not (
                isinstance(value, dict)
                and value.get("$type") == PAGINATION_REQUEST_TYPE
                and value.get("pagerId") == SKILLS_PAGER_ID
            ):
                continue
            requested_arguments = value.get("requestedArguments")
            if not isinstance(requested_arguments, dict):
                continue
            payload = requested_arguments.get("payload")
            if not (
                isinstance(payload, dict)
                and payload.get("filter") == SKILLS_ALL_FILTER
            ):
                continue
            return SduiPaginationRequest(
                pager_id=SKILLS_PAGER_ID,
                requested_arguments=requested_arguments,
                raw_request=value,
            )
    return None


def extract_projects_pagination_request(
    document: ComoFlightDocument,
) -> SduiPaginationRequest | None:
    """Return the Projects pager embedded in a details or page response."""

    return _extract_pagination_request(document, PROJECTS_PAGER_ID)


def extract_education_pagination_request(
    document: ComoFlightDocument,
) -> SduiPaginationRequest | None:
    """Return the Education pager embedded in a details or page response."""

    return _extract_pagination_request(document, EDUCATION_PAGER_ID)


def _extract_pagination_request(
    document: ComoFlightDocument,
    pager_id: str,
) -> SduiPaginationRequest | None:
    for root in document.records.values():
        for value in _walk(root):
            if not (
                isinstance(value, dict)
                and value.get("$type") == PAGINATION_REQUEST_TYPE
                and value.get("pagerId") == pager_id
            ):
                continue
            requested_arguments = value.get("requestedArguments")
            if isinstance(requested_arguments, dict):
                return SduiPaginationRequest(
                    pager_id=pager_id,
                    requested_arguments=requested_arguments,
                    raw_request=value,
                )
    return None


def extract_experiences_from_flight(
    document: ComoFlightDocument,
) -> list[Position]:
    """Normalize experience cards from a hydrated experience component."""

    experiences: list[Position] = []
    seen: set[tuple[str, str, int]] = set()
    organization_names: dict[str, str] = {}
    for root in document.records.values():
        organization_urls = _organization_urls(root)
        if len(organization_urls) != 1:
            continue
        texts = _clean_visible_text(root, document)
        if not texts or any(_parse_experience_dates(text) for text in texts[1:]):
            continue
        organization_names.setdefault(next(iter(organization_urls)), texts[0])

    for root in document.records.values():
        organization_urls = _organization_urls(root)
        if len(organization_urls) != 1:
            continue
        texts = _clean_visible_text(root, document)
        if len(texts) < 2:
            continue
        dated_entry = next(
            (
                (index, parsed)
                for index, text in enumerate(texts[1:], start=1)
                if (parsed := _parse_experience_dates(text)) is not None
            ),
            None,
        )
        if dated_entry is None:
            continue
        date_index, dates = dated_entry
        start_date, end_date = dates
        title = texts[0]
        company_url = next(iter(organization_urls))
        company_name = (
            texts[1].split(" · ", maxsplit=1)[0]
            if date_index > 1
            else organization_names.get(company_url)
        )
        if not company_name:
            continue
        identity = (title, company_name, start_date.year or 0)
        if identity in seen:
            continue
        seen.add(identity)
        location = (
            texts[date_index + 1].split(" · ", maxsplit=1)[0]
            if len(texts) > date_index + 1
            else None
        )
        experiences.append(
            Position(
                title=title,
                company_name=company_name,
                company_url=company_url,
                location=location,
                start_date=start_date,
                end_date=end_date,
            )
        )
    return experiences


def _organization_urls(value: JSONValue) -> set[str]:
    return {
        nested
        for nested in _walk(value)
        if isinstance(nested, str)
        and nested.startswith(
            (
                "https://www.linkedin.com/company/",
                "https://www.linkedin.com/school/",
            )
        )
    }


def extract_education_from_flight(
    document: ComoFlightDocument,
) -> list[Education]:
    """Normalize education cards from a hydrated lower-profile component."""

    education: list[Education] = []
    seen: set[tuple[str, str | None, int]] = set()
    for root in document.records.values():
        for candidate in _walk(root):
            if not isinstance(candidate, (dict, list)):
                continue
            school_urls = {
                value
                for value in _walk(candidate)
                if isinstance(value, str)
                and value.startswith("https://www.linkedin.com/school/")
            }
            candidate_props = _react_props(candidate)
            component_key = (
                candidate_props.get("componentKey")
                if candidate_props is not None
                else None
            )
            is_detail_row = (
                isinstance(component_key, str)
                and UUID_COMPONENT_KEY_PATTERN.fullmatch(component_key) is not None
            )
            if len(school_urls) != 1 and not is_detail_row:
                continue
            texts = [
                text.strip()
                for text in _component_content_text(candidate, document)
                if text.strip()
            ]
            if not texts:
                continue
            school_name = texts[0]
            dated_entry = next(
                (
                    (index, parsed)
                    for index, text in enumerate(texts[1:], start=1)
                    if (parsed := _parse_education_dates(text)) is not None
                ),
                None,
            )
            if is_detail_row and dated_entry is None:
                continue
            date_index, dates = (
                dated_entry if dated_entry is not None else (len(texts), None)
            )
            program = texts[1] if len(texts) > 1 and date_index > 1 else None
            degree_name, field_of_study = _split_education_program(program)
            start_date, end_date = dates if dates is not None else (None, None)
            identity = (
                school_name,
                degree_name,
                start_date.year if start_date is not None and start_date.year else 0,
            )
            if identity in seen:
                continue
            seen.add(identity)
            education.append(
                Education(
                    school_name=school_name,
                    degree_name=degree_name,
                    field_of_study=field_of_study,
                    start_date=start_date,
                    end_date=end_date,
                )
            )
    return education


def extract_skills_from_flight(document: ComoFlightDocument) -> list[str]:
    """Return skill names from stable per-skill SDUI component roots."""

    skills: list[str] = []
    for root in document.records.values():
        for candidate in _walk(root):
            if not (
                isinstance(candidate, list)
                and len(candidate) >= 4
                and isinstance(candidate[3], dict)
                and isinstance(candidate[3].get("componentKey"), str)
                and str(candidate[3]["componentKey"]).startswith(
                    "com.linkedin.sdui.profile.skill("
                )
                and not str(candidate[3]["componentKey"]).endswith("-divider")
            ):
                continue
            texts = [
                text.strip()
                for text in _component_content_text(candidate, document)
                if text.strip()
            ]
            if texts and texts[0] not in skills:
                skills.append(texts[0])
    return skills


def extract_projects_from_flight(document: ComoFlightDocument) -> list[Project]:
    """Normalize Projects from SDUI rows separated by divider elements."""

    projects: list[Project] = []
    seen_titles: set[str] = set()
    for root in document.records.values():
        resolved_root = _resolve_references(root, document)
        for value in _walk(resolved_root):
            if not isinstance(value, list) or _is_react_element(value):
                continue
            if not any(_is_divider(child) for child in value):
                continue
            for row in (child for child in value if not _is_divider(child)):
                project = _parse_project_row(row, document)
                if project is None or project.title in seen_titles:
                    continue
                seen_titles.add(project.title)
                projects.append(project)
            if projects:
                return projects

    # A single-project response has no divider, so parse its content container.
    for root in document.records.values():
        resolved_root = _resolve_references(root, document)
        project = _parse_project_row(resolved_root, document)
        if project is not None:
            return [project]
    return []


def _parse_project_row(
    value: JSONValue,
    document: ComoFlightDocument,
) -> Project | None:
    texts = _clean_visible_text(value, document)
    if not texts or texts[0] in {"Projects", "Project link", "GitHub"}:
        return None
    title = texts[0]
    dates = next(
        (
            parsed
            for text in texts[1:]
            if (parsed := _parse_experience_dates(text)) is not None
        ),
        None,
    )
    skill_marker = next(
        (index for index, text in enumerate(texts) if text == "Skills:"),
        None,
    )
    skills: list[str] = []
    skill_summary: str | None = None
    if skill_marker is not None and skill_marker + 1 < len(texts):
        skill_summary = texts[skill_marker + 1]
        skills = [
            skill.strip()
            for skill in skill_summary.split(",")
            if skill.strip() and not re.fullmatch(r"\+\d+ skills?", skill.strip())
        ]
    ignored = {title, "Skills:", "GitHub", "Project link"}
    if skill_summary is not None:
        ignored.add(skill_summary)
    description_parts = [
        text
        for text in texts[1:]
        if text not in ignored and _parse_experience_dates(text) is None
    ]
    start_date, end_date = dates if dates is not None else (None, None)
    return Project(
        title=title,
        description="\n".join(description_parts) or None,
        url=_find_external_url(value),
        skills=skills,
        start_date=start_date,
        end_date=end_date,
    )


def _is_divider(value: JSONValue) -> bool:
    return _is_react_element(value) and isinstance(value, list) and value[1] == "hr"


def extract_about_from_flight(document: ComoFlightDocument) -> str | None:
    """Return the paragraphs from the stable About profile-card component."""

    for root in _component_roots(document, "About"):
        texts = _clean_visible_text(root, document)
        paragraphs = [text for text in texts if text.casefold() != "about"]
        if paragraphs:
            return "\n\n".join(paragraphs)
    return None


def extract_certifications_from_flight(
    document: ComoFlightDocument,
) -> list[Certification]:
    """Normalize license and certification cards from the Part-1 component."""

    certifications: list[Certification] = []
    seen: set[tuple[str, str]] = set()
    for section in _component_roots(document, "CertificationTopLevel"):
        resolved_section = _resolve_references(section, document)
        for node in _walk(resolved_section):
            if not _is_react_element(node):
                continue
            company_urls = {
                value
                for value in _walk(node)
                if isinstance(value, str)
                and value.startswith("https://www.linkedin.com/company/")
            }
            if len(company_urls) != 1:
                continue
            texts = _clean_visible_text(node, document)
            if len(texts) < 2:
                continue
            name, authority = texts[0], texts[1]
            identity = (name, authority)
            if identity in seen:
                continue
            seen.add(identity)
            issued = next(
                (text for text in texts[2:] if text.startswith("Issued ")),
                None,
            )
            start_date, end_date = _parse_certification_dates(issued)
            credential = next(
                (
                    text.removeprefix("Credential ID ").strip()
                    for text in texts[2:]
                    if text.startswith("Credential ID ")
                ),
                None,
            )
            certifications.append(
                Certification(
                    name=name,
                    authority=authority,
                    license_number=credential,
                    url=_find_credential_url(node),
                    start_date=start_date,
                    end_date=end_date,
                )
            )
    return certifications


def extract_languages_from_flight(
    document: ComoFlightDocument,
) -> list[Language]:
    """Normalize language/proficiency pairs from the Part-4 component."""

    languages: list[Language] = []
    seen_names: set[str] = set()
    for section in _component_roots(document, "LanguageTopLevel"):
        resolved_section = _resolve_references(section, document)
        divided_rows = _language_rows_separated_by_dividers(
            resolved_section,
            document,
        )
        if divided_rows:
            for name, proficiency in divided_rows:
                if name in seen_names:
                    continue
                seen_names.add(name)
                languages.append(Language(name=name, proficiency=proficiency))
            continue

        section_language_count = len(languages)
        for node in _walk(resolved_section):
            if not _is_react_element(node):
                continue
            assert isinstance(node, list)
            texts = _clean_visible_text(node, document)
            if len(texts) != 2 or _has_nested_text_pair(node, document):
                continue
            name, proficiency = texts
            if name in seen_names:
                continue
            seen_names.add(name)
            languages.append(Language(name=name, proficiency=proficiency))
        if len(languages) == section_language_count:
            texts = _clean_visible_text(resolved_section, document)
            if len(texts) == 1 and texts[0] not in seen_names:
                seen_names.add(texts[0])
                languages.append(Language(name=texts[0]))
    return languages


def _language_rows_separated_by_dividers(
    section: JSONValue,
    document: ComoFlightDocument,
) -> list[tuple[str, str | None]]:
    for value in _walk(section):
        if not isinstance(value, list) or _is_react_element(value):
            continue
        if not any(
            _is_react_element(child)
            and isinstance(child, list)
            and child[1] == "hr"
            for child in value
        ):
            continue
        rows: list[tuple[str, str | None]] = []
        for child in value:
            if not _is_react_element(child):
                continue
            assert isinstance(child, list)
            if child[1] == "hr":
                continue
            texts = _clean_visible_text(child, document)
            if len(texts) in {1, 2}:
                rows.append((texts[0], texts[1] if len(texts) == 2 else None))
        if rows:
            return rows
    return []


def _split_education_program(program: str | None) -> tuple[str | None, str | None]:
    if program is None:
        return None, None
    parts = [part.strip() for part in program.split(",") if part.strip()]
    if len(parts) < 2 or (
        len(parts) == 2 and parts[-1].casefold() in DEGREE_TRAILING_QUALIFIERS
    ):
        return program, None
    return ", ".join(parts[:-1]), parts[-1]


def _has_nested_text_pair(
    node: list[JSONValue],
    document: ComoFlightDocument,
) -> bool:
    for nested in _walk(node[3]):
        if nested is node or not _is_react_element(nested):
            continue
        if len(_clean_visible_text(nested, document)) == 2:
            return True
    return False


def _parse_experience_dates(value: str) -> tuple[DateParts, DateParts | None] | None:
    match = EXPERIENCE_DATE_PATTERN.match(value)
    if match is None:
        single_date_match = EXPERIENCE_SINGLE_DATE_PATTERN.match(value)
        if single_date_match is None:
            return None
        single_date = DateParts(
            year=int(single_date_match.group("start_year")),
            month=MONTH_NUMBERS.get(single_date_match.group("start_month")),
        )
        return single_date, single_date.model_copy()
    start_month = MONTH_NUMBERS.get(match.group("start_month"))
    end_month_name = match.group("end_month")
    return (
        DateParts(year=int(match.group("start_year")), month=start_month),
        (
            DateParts(
                year=int(match.group("end_year")),
                month=MONTH_NUMBERS.get(end_month_name),
            )
            if match.group("end_year")
            else None
        ),
    )


def _parse_education_dates(value: str) -> tuple[DateParts, DateParts] | None:
    match = EDUCATION_DATE_PATTERN.match(value)
    if match is None:
        return None
    return (
        DateParts(
            year=int(match.group("start_year")),
            month=MONTH_NUMBERS.get(match.group("start_month")),
        ),
        DateParts(
            year=int(match.group("end_year")),
            month=MONTH_NUMBERS.get(match.group("end_month")),
        ),
    )


def _parse_certification_dates(
    value: str | None,
) -> tuple[DateParts | None, DateParts | None]:
    if value is None:
        return None, None
    match = CERTIFICATION_DATE_PATTERN.match(value)
    if match is None:
        return None, None
    start_date = DateParts(
        year=int(match.group("start_year")),
        month=MONTH_NUMBERS.get(match.group("start_month")),
    )
    end_year = match.group("end_year")
    end_date = (
        DateParts(
            year=int(end_year),
            month=MONTH_NUMBERS.get(match.group("end_month")),
        )
        if end_year
        else None
    )
    return start_date, end_date


def extract_profile_from_como(html: str, public_identifier: str) -> LinkedInProfile:
    """Normalize the eagerly hydrated profile top card into the public schema."""

    document = parse_como_flight(html)
    top_card = _find_mapping_with_value(
        document.records.values(),
        "observabilityIdentifier",
        TOP_CARD_IDENTIFIER,
    )
    if top_card is None:
        raise ComoFlightParseError("Profile top-card component is missing")

    initial_content = _find_string_by_key([top_card], ("initialContent",))
    if initial_content is None:
        raise ComoFlightParseError("Profile top-card content reference is missing")
    root_id = _reference_id(initial_content)
    if root_id is None or root_id not in document.records:
        raise ComoFlightParseError("Profile top-card content reference is invalid")

    values = [
        document.records[record_id]
        for record_id in document.reachable_record_ids_in_order(root_id)
    ]
    first_name = _find_string_by_key(
        values,
        ("givenName", "vieweeFirstName", "firstName"),
    )
    last_name = _find_string_by_key(values, ("familyName", "lastName"))
    full_name = _find_semantic_text(values, "h2", document)
    if full_name is None:
        full_name = " ".join(part for part in (first_name, last_name) if part) or None

    profile_images = _find_profile_images(values, document)
    return LinkedInProfile(
        public_identifier=public_identifier,
        profile_url=f"https://www.linkedin.com/in/{public_identifier}/",
        first_name=first_name,
        last_name=last_name,
        full_name=full_name,
        headline=_find_semantic_text(values, "p", document, prefer_last=True),
        location=_find_contact_row_location(values, document),
        has_profile_photo_frame=any(
            "profile-framedphoto" in image.url for image in profile_images
        ),
        profile_images=profile_images,
    )


def _reference_id(value: str) -> str | None:
    match = FLIGHT_REFERENCE_PATTERN.fullmatch(value)
    return match.group(1).lower() if match else None


def _reference_ids(value: JSONValue) -> list[str]:
    references: list[str] = []
    if isinstance(value, str):
        reference_id = _reference_id(value)
        if reference_id is not None:
            references.append(reference_id)
    elif isinstance(value, list):
        for item in value:
            references.extend(_reference_ids(item))
    elif isinstance(value, dict):
        for item in value.values():
            references.extend(_reference_ids(item))
    return references


def _walk(value: JSONValue) -> list[JSONValue]:
    values = [value]
    if isinstance(value, list):
        for item in value:
            values.extend(_walk(item))
    elif isinstance(value, dict):
        for item in value.values():
            values.extend(_walk(item))
    return values


def _is_react_element(value: JSONValue) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 4
        and value[0] == "$"
        and isinstance(value[3], dict)
    )


def _react_props(value: JSONValue) -> dict[str, JSONValue] | None:
    if not isinstance(value, list) or len(value) < 4 or value[0] != "$":
        return None
    props = value[3]
    return props if isinstance(props, dict) else None


def _component_roots(
    document: ComoFlightDocument,
    component_suffix: str,
) -> list[list[JSONValue]]:
    roots: list[list[JSONValue]] = []
    for root in document.records.values():
        if not isinstance(root, list) or not _is_react_element(root):
            continue
        props = root[3]
        assert isinstance(props, dict)
        component_key = props.get("componentKey") or props.get("componentkey")
        if isinstance(component_key, str) and component_key.endswith(component_suffix):
            roots.append(root)
    return roots


def _resolve_references(
    value: JSONValue,
    document: ComoFlightDocument,
    visited: frozenset[str] = frozenset(),
) -> JSONValue:
    if isinstance(value, str):
        reference_id = _reference_id(value)
        if (
            reference_id is not None
            and reference_id in document.records
            and reference_id not in visited
        ):
            return _resolve_references(
                document.records[reference_id],
                document,
                visited | {reference_id},
            )
        return value
    if isinstance(value, list):
        return [_resolve_references(item, document, visited) for item in value]
    if isinstance(value, dict):
        return {
            key: _resolve_references(item, document, visited)
            for key, item in value.items()
        }
    return value


def _clean_visible_text(
    value: JSONValue,
    document: ComoFlightDocument,
) -> list[str]:
    return [
        cleaned
        for text in _visible_text(value, document)
        if (cleaned := " ".join(text.split()))
    ]


def _component_content_text(
    value: JSONValue,
    document: ComoFlightDocument,
) -> list[str]:
    """Read a component's deferred content before its ordinary children."""

    props = _react_props(value)
    if props is not None:
        for key in ("initialContent", "children"):
            if key not in props:
                continue
            texts = _visible_text(props[key], document)
            if texts:
                return texts
    return _visible_text(value, document)


def _find_credential_url(value: JSONValue) -> str | None:
    return _find_external_url(value)


def _find_external_url(value: JSONValue) -> str | None:
    direct_external_urls: list[str] = []
    for nested in _walk(value):
        if not isinstance(nested, dict):
            continue
        candidate = nested.get("url")
        if not isinstance(candidate, str) or not candidate.startswith(
            ("http://", "https://")
        ):
            continue
        parsed = urlsplit(candidate)
        if parsed.netloc.casefold().endswith("linkedin.com"):
            if parsed.path != "/safety/go/":
                continue
            target = parse_qs(parsed.query).get("url", [None])[0]
            if target and urlsplit(target).scheme in {"http", "https"}:
                return target
            continue
        direct_external_urls.append(candidate)
    return direct_external_urls[0] if direct_external_urls else None


def _find_mapping_with_value(
    roots: Iterable[JSONValue],
    key: str,
    expected: str,
) -> dict[str, JSONValue] | None:
    for root in roots:
        for value in _walk(root):
            if isinstance(value, dict) and value.get(key) == expected:
                return value
    return None


def _find_string_by_key(
    roots: Iterable[JSONValue],
    keys: tuple[str, ...],
) -> str | None:
    for root in roots:
        for value in _walk(root):
            if not isinstance(value, dict):
                continue
            for key in keys:
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
    return None


def _find_semantic_text(
    roots: list[JSONValue],
    tag: str,
    document: ComoFlightDocument,
    *,
    prefer_last: bool = False,
) -> str | None:
    candidates: list[str] = []
    for root in roots:
        for value in _walk(root):
            if (
                isinstance(value, list)
                and len(value) >= 4
                and value[0] == "$"
                and value[1] == tag
                and isinstance(value[3], dict)
            ):
                texts = _visible_text(value[3].get("children"), document)
                if texts:
                    candidates.append(" ".join(texts).strip())
    if not candidates:
        return None
    return candidates[-1] if prefer_last else candidates[0]


def _visible_text(
    value: JSONValue,
    document: ComoFlightDocument,
    visited: set[str] | None = None,
) -> list[str]:
    visited = set() if visited is None else visited
    if isinstance(value, str):
        reference_id = _reference_id(value)
        if reference_id is not None and reference_id in document.records:
            if reference_id in visited:
                return []
            visited.add(reference_id)
            return _visible_text(document.records[reference_id], document, visited)
        return [] if value.startswith("$") else [value]
    if isinstance(value, list):
        if (
            len(value) >= 4
            and value[0] == "$"
            and isinstance(value[3], dict)
        ):
            props = value[3]
            text_props = props.get("textProps")
            if isinstance(text_props, dict) and "children" in text_props:
                return _visible_text(text_props["children"], document, visited)
            return _visible_text(props.get("children"), document, visited)
        texts: list[str] = []
        for item in value:
            texts.extend(_visible_text(item, document, visited))
        return texts
    return []


def _contains_contact_link(value: JSONValue) -> bool:
    return any(
        isinstance(nested, str) and nested.endswith(CONTACT_INFO_PATH_SUFFIX)
        for nested in _walk(value)
    )


def _find_contact_row_location(
    roots: list[JSONValue],
    document: ComoFlightDocument,
) -> str | None:
    for root in roots:
        resolved_root = _resolve_references(root, document)
        sibling_groups = [
            value
            for value in _walk(resolved_root)
            if isinstance(value, list) and not _is_react_element(value)
        ]
        for value in reversed(sibling_groups):
            for index, child in enumerate(value):
                if not _contains_contact_link(child):
                    continue
                for preceding in reversed(value[:index]):
                    for text in reversed(_visible_text(preceding, document)):
                        cleaned = text.strip()
                        if any(character.isalpha() for character in cleaned):
                            return cleaned
    return None


def _find_profile_images(
    roots: list[JSONValue],
    document: ComoFlightDocument,
) -> list[ProfileImage]:
    photo_node = _find_mapping_with_value(roots, "aria-label", "Profile photo")
    if photo_node is None:
        return []

    referenced_ids = _reference_ids(photo_node)
    candidates: list[JSONValue] = [photo_node]
    for reference_id in referenced_ids:
        if reference_id in document.records:
            candidates.extend(
                document.records[item]
                for item in document.reachable_record_ids(reference_id)
            )

    images: list[ProfileImage] = []
    seen_urls: set[str] = set()
    for candidate in candidates:
        for value in _walk(candidate):
            if not isinstance(value, dict):
                continue
            render_payload = value.get("renderPayload")
            if not isinstance(render_payload, dict):
                continue
            root_url = render_payload.get("rootUrl")
            renditions = render_payload.get("imageRenditions")
            if not isinstance(root_url, str) or not isinstance(renditions, list):
                continue
            for rendition in renditions:
                if not isinstance(rendition, dict):
                    continue
                suffix = rendition.get("suffixUrl")
                if not isinstance(suffix, str):
                    continue
                url = f"{root_url}{suffix}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                width = rendition.get("width")
                height = rendition.get("height")
                images.append(
                    ProfileImage(
                        url=url,
                        width=width if isinstance(width, int) else None,
                        height=height if isinstance(height, int) else None,
                    )
                )
    return images
