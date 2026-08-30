import json

import pytest

from tross_linkedin_api.clients.linkedin import ProfilePageDocument
from tross_linkedin_api.clients.ssr import SsrLinkedInProfileClient
from tross_linkedin_api.parsers.como import (
    ComoFlightDocument,
    ComoFlightParseError,
    SduiComponentRequest,
    SduiPaginationRequest,
    extract_about_from_flight,
    extract_certifications_from_flight,
    extract_component_requests,
    extract_courses_from_flight,
    extract_courses_pagination_request,
    extract_education_from_flight,
    extract_experiences_from_flight,
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
    parse_como_flight,
    parse_flight_stream,
)
from tross_linkedin_api.schemas.profile import ProfileRequest


def hydration_html(records: list[str]) -> str:
    stream = flight_stream(records)
    midpoint = len(stream) // 2
    chunks = [stream[:midpoint], stream[midpoint:]]
    return (
        "<script>window.__como_rehydration__ = "
        f"{json.dumps(chunks)};"
        "</script>"
    )


def flight_stream(records: list[str]) -> str:
    return "\n".join(records) + "\n"


def test_parse_como_flight_reassembles_chunks_and_references() -> None:
    html = hydration_html(
        [
            '0:["$","div",null,{"children":"$L1"}]',
            '1:["$","p",null,{"children":["Example headline"]}]',
            '2:I[123,["module"],"default"]',
        ]
    )

    document = parse_como_flight(html)

    assert document.record_count == 3
    assert document.resolve_reference("$L1") == [
        "$",
        "p",
        None,
        {"children": ["Example headline"]},
    ]
    assert document.reachable_record_ids("0") == {"0", "1"}
    assert document.reachable_record_ids_in_order("0") == ["0", "1"]


def test_parse_flight_preserves_non_json_record_as_opaque() -> None:
    document = parse_flight_stream(
        '0:["$","div",null,{"children":[]}]\n'
        '1:["unsupported \\u{1f600} escape"]\n'
    )

    assert document.record_count == 2
    assert document.opaque_record_ids == frozenset({"1"})


def test_parse_flight_uses_only_newline_as_record_delimiter() -> None:
    document = parse_flight_stream('0:["first\u0085second"]\n')

    assert document.records["0"] == ["first\u0085second"]


def test_extract_component_request_descriptor() -> None:
    html = hydration_html(
        [
            (
                '0:["$","div",null,{"request":{"$type":'
                '"proto.sdui.actions.core.AsyncComponentRequest",'
                '"newComponentId":"profileCardsExperienceOnly",'
                '"requestedArguments":{"$type":"RequestedArguments",'
                '"requestedStateKeys":[],"payload":{"vanityName":"ada-lovelace"},'
                '"requestMetadata":{"$type":"RequestMetadata"}}}}]'
            )
        ]
    )

    requests = extract_component_requests(html)

    assert len(requests) == 1
    assert requests[0].component_id == "profileCardsExperienceOnly"
    assert requests[0].client_arguments() == {
        "payload": {"vanityName": "ada-lovelace"},
        "states": [],
        "requestMetadata": {"$type": "RequestMetadata"},
        "screenId": "",
        "knownTemplateIds": [],
    }


def test_extract_experiences_from_component_flight_stream() -> None:
    stream = flight_stream(
        [
            (
                '0:["$","div",null,{"children":["$L1","$L6"]}]'
            ),
            (
                '1:["$","button",null,{"url":'
                '"https://www.linkedin.com/company/123/",'
                '"children":["$L2","$L3","$L4","$L5"]}]'
            ),
            '2:["$","span",null,{"children":["Engineer"]}]',
            '3:["$","span",null,{"children":["Example Corp · Full-time"]}]',
            '4:["$","span",null,{"children":["Jan 2024 - Present · 2 yrs"]}]',
            '5:["$","span",null,{"children":["London · Hybrid"]}]',
            (
                '6:["$","button",null,{"url":'
                '"https://www.linkedin.com/school/456/",'
                '"children":["$L7","$L8","$L9"]}]'
            ),
            '7:["$","span",null,{"children":["Teaching Assistant"]}]',
            '8:["$","span",null,{"children":["Example University"]}]',
            '9:["$","span",null,{"children":["Feb 2022 - Dec 2023"]}]',
        ]
    )

    document = parse_flight_stream(stream)
    experiences = extract_experiences_from_flight(document)

    assert [experience.model_dump() for experience in experiences] == [
        {
            "title": "Engineer",
            "company_name": "Example Corp",
            "company_url": "https://www.linkedin.com/company/123/",
            "location": "London",
            "description": None,
            "start_date": {"year": 2024, "month": 1},
            "end_date": None,
        },
        {
            "title": "Teaching Assistant",
            "company_name": "Example University",
            "company_url": "https://www.linkedin.com/school/456/",
            "location": None,
            "description": None,
            "start_date": {"year": 2022, "month": 2},
            "end_date": {"year": 2023, "month": 12},
        },
    ]


def test_extract_experiences_accepts_year_only_en_dash_dates() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","button",null,{"url":'
                    '"https://www.linkedin.com/company/123/",'
                    '"children":["$L1","$L2","$L3"]}]'
                ),
                '1:["$","span",null,{"children":["Founder"]}]',
                '2:["$","span",null,{"children":["Example Company"]}]',
                '3:["$","span",null,{"children":["2015 – Present"]}]',
            ]
        )
    )

    assert [item.model_dump() for item in extract_experiences_from_flight(document)] == [
        {
            "title": "Founder",
            "company_name": "Example Company",
            "company_url": "https://www.linkedin.com/company/123/",
            "location": None,
            "description": None,
            "start_date": {"year": 2015, "month": None},
            "end_date": None,
        }
    ]


def test_extract_experiences_accepts_single_month_duration() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","button",null,{"url":'
                    '"https://www.linkedin.com/company/123/",'
                    '"children":["$L1","$L2","$L3","$L4"]}]'
                ),
                '1:["$","span",null,{"children":["Data Science Intern"]}]',
                (
                    '2:["$","span",null,{"children":'
                    '["Navodita Infotech · Internship"]}]'
                ),
                '3:["$","span",null,{"children":["Oct 2023 · 1 mo"]}]',
                '4:["$","span",null,{"children":["Remote"]}]',
            ]
        )
    )

    assert [item.model_dump() for item in extract_experiences_from_flight(document)] == [
        {
            "title": "Data Science Intern",
            "company_name": "Navodita Infotech",
            "company_url": "https://www.linkedin.com/company/123/",
            "location": "Remote",
            "description": None,
            "start_date": {"year": 2023, "month": 10},
            "end_date": {"year": 2023, "month": 10},
        }
    ]


def test_extract_experiences_inherits_company_from_grouped_card() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","div",null,{"url":'
                    '"https://www.linkedin.com/company/123/",'
                    '"children":["$L1","$L2"]}]'
                ),
                '1:["$","span",null,{"children":["Example Company"]}]',
                '2:["$","span",null,{"children":["22 yrs 5 mos"]}]',
                (
                    '3:["$","div",null,{"url":'
                    '"https://www.linkedin.com/company/123/",'
                    '"children":["$L4","$L5"]}]'
                ),
                '4:["$","span",null,{"children":["CEO"]}]',
                '5:["$","span",null,{"children":["2015 – Present"]}]',
                (
                    '6:["$","div",null,{"url":'
                    '"https://www.linkedin.com/company/123/",'
                    '"children":["$L7","$L8"]}]'
                ),
                (
                    '7:["$","span",null,{"children":'
                    '["Product Management + Leadership"]}]'
                ),
                '8:["$","span",null,{"children":["Apr 2004 - 2015"]}]',
            ]
        )
    )

    assert [item.model_dump() for item in extract_experiences_from_flight(document)] == [
        {
            "title": "CEO",
            "company_name": "Example Company",
            "company_url": "https://www.linkedin.com/company/123/",
            "location": None,
            "description": None,
            "start_date": {"year": 2015, "month": None},
            "end_date": None,
        },
        {
            "title": "Product Management + Leadership",
            "company_name": "Example Company",
            "company_url": "https://www.linkedin.com/company/123/",
            "location": None,
            "description": None,
            "start_date": {"year": 2004, "month": 4},
            "end_date": {"year": 2015, "month": None},
        },
    ]


def test_extract_education_from_component_flight_stream() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","button",null,{"url":'
                    '"https://www.linkedin.com/school/123/",'
                    '"children":["$L1","$L2","$L3"]}]'
                ),
                '1:["$","span",null,{"children":["Example University"]}]',
                (
                    '2:["$","span",null,{"children":'
                    '["Master of Science - MS, Computer Science"]}]'
                ),
                '3:["$","span",null,{"children":["Aug 2022 – Jul 2024"]}]',
                (
                    '4:["$","button",null,{"url":'
                    '"https://www.linkedin.com/school/456/",'
                    '"children":["$L5","$L6","$L7"]}]'
                ),
                '5:["$","span",null,{"children":["Second University"]}]',
                '6:["$","span",null,{"children":["Bachelor of Arts"]}]',
                '7:["$","span",null,{"children":["2018 – 2021"]}]',
            ]
        )
    )

    education = extract_education_from_flight(document)

    assert [item.model_dump() for item in education] == [
        {
            "school_name": "Example University",
            "degree_name": "Master of Science - MS",
            "field_of_study": "Computer Science",
            "start_date": {"year": 2022, "month": 8},
            "end_date": {"year": 2024, "month": 7},
        },
        {
            "school_name": "Second University",
            "degree_name": "Bachelor of Arts",
            "field_of_study": None,
            "start_date": {"year": 2018, "month": None},
            "end_date": {"year": 2021, "month": None},
        },
    ]


def test_extract_education_preserves_partial_and_qualified_degree_cards() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","button",null,{"url":'
                    '"https://www.linkedin.com/school/123/",'
                    '"children":["$L1","$L2","$L8"]}]'
                ),
                '1:["$","span",null,{"children":["Example University"]}]',
                '2:["$","span",null,{"children":["1973 – 1975"]}]',
                '8:["$","p",null,{"children":["Research description"]}]',
                (
                    '3:["$","button",null,{"url":'
                    '"https://www.linkedin.com/school/456/",'
                    '"children":["$L4","$L5"]}]'
                ),
                '4:["$","span",null,{"children":["Second University"]}]',
                (
                    '5:["$","span",null,{"children":'
                    '["Bachelor of Arts, Honours"]}]'
                ),
                (
                    '6:["$","button",null,{"url":'
                    '"https://www.linkedin.com/school/789/",'
                    '"children":["$L7"]}]'
                ),
                '7:["$","span",null,{"children":["Lakeside School"]}]',
            ]
        )
    )

    assert [item.model_dump() for item in extract_education_from_flight(document)] == [
        {
            "school_name": "Example University",
            "degree_name": None,
            "field_of_study": None,
            "start_date": {"year": 1973, "month": None},
            "end_date": {"year": 1975, "month": None},
        },
        {
            "school_name": "Second University",
            "degree_name": "Bachelor of Arts, Honours",
            "field_of_study": None,
            "start_date": None,
            "end_date": None,
        },
        {
            "school_name": "Lakeside School",
            "degree_name": None,
            "field_of_study": None,
            "start_date": None,
            "end_date": None,
        },
    ]


def test_extract_education_ignores_nested_section_wrapper() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","section",null,{"componentKey":'
                    '"11111111-1111-1111-1111-111111111111",'
                    '"initialContent":["Education","First University",'
                    '"First Degree","2019 – 2023","Second University",'
                    '"Second Degree","2024 – 2025"]}]'
                ),
                (
                    '1:["$","div",null,{"componentKey":'
                    '"22222222-2222-2222-2222-222222222222",'
                    '"initialContent":["First University","First Degree",'
                    '"2019 – 2023"]}]'
                ),
                (
                    '2:["$","div",null,{"componentKey":'
                    '"33333333-3333-3333-3333-333333333333",'
                    '"initialContent":["Second University","Second Degree",'
                    '"2024 – 2025"]}]'
                ),
            ]
        )
    )

    assert [
        item.school_name for item in extract_education_from_flight(document)
    ] == ["First University", "Second University"]


def test_extract_skills_from_component_flight_stream() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","div",null,{"componentKey":'
                    '"com.linkedin.sdui.profile.skill(member, 1)",'
                    '"children":["$L1","$L2"]}]'
                ),
                '1:["$","span",null,{"children":["Python"]}]',
                '2:["$","span",null,{"children":["Engineer at Example Corp"]}]',
                (
                    '3:["$","div",null,{"componentKey":'
                    '"com.linkedin.sdui.profile.skill(member, 2)",'
                    '"children":["$L4"]}]'
                ),
                '4:["$","span",null,{"children":["FastAPI"]}]',
            ]
        )
    )

    assert extract_skills_from_flight(document) == ["Python", "FastAPI"]


def test_extract_test_scores_from_paginated_rows() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","div",null,{"children":['
                    '["$","div",null,{"children":["$L1","$L2","$L3"]}],'
                    '["$","hr",null,{}],'
                    '["$","div",null,{"children":["$L4","$L5"]}]]}]'
                ),
                '1:["$","p",null,{"children":["BITS HD Test"]}]',
                '2:["$","p",null,{"children":["Score: 119 · Aug 2021"]}]',
                (
                    '3:["$","p",null,{"children":'
                    '["Attempted Software Systems."]}]'
                ),
                '4:["$","p",null,{"children":["GATE - CS/IT"]}]',
                '5:["$","p",null,{"children":["Score: 700 · Feb 2021"]}]',
            ]
        )
    )

    assert [item.model_dump() for item in extract_test_scores_from_flight(document)] == [
        {
            "name": "BITS HD Test",
            "score": "119",
            "date": "Aug 2021",
            "description": "Attempted Software Systems.",
        },
        {
            "name": "GATE - CS/IT",
            "score": "700",
            "date": "Feb 2021",
            "description": None,
        },
    ]


def test_extract_publications_from_paginated_rows() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","div",null,{"children":['
                    '["$","div",null,{"children":["$L1","$L2","$L3","$L4"]}],'
                    '["$","hr",null,{}],'
                    '["$","div",null,{"children":["$L5","$L6"]}]]}]'
                ),
                (
                    '1:["$","a",null,{"url":'
                    '"https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fexample.com%2Fpaper",'
                    '"children":["Paper One"]}]'
                ),
                (
                    '2:["$","p",null,{"children":'
                    '["Example Journal · Jun 12, 2020"]}]'
                ),
                '3:["$","p",null,{"children":["Paper description."]}]',
                '4:["$","p",null,{"children":["Other authors"]}]',
                '5:["$","p",null,{"children":["Paper Two"]}]',
                (
                    '6:["$","p",null,{"children":'
                    '["Second Journal · May 2, 2020"]}]'
                ),
            ]
        )
    )

    assert [item.model_dump() for item in extract_publications_from_flight(document)] == [
        {
            "title": "Paper One",
            "publisher": "Example Journal",
            "published_on": "Jun 12, 2020",
            "description": "Paper description.",
            "url": "https://example.com/paper",
        },
        {
            "title": "Paper Two",
            "publisher": "Second Journal",
            "published_on": "May 2, 2020",
            "description": None,
            "url": None,
        },
    ]


def test_extract_given_recommendations_from_rows() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","div",null,{"children":['
                    '["$","div",null,{"children":["$L1","$L2","$L3","$L4"]}],'
                    '["$","hr",null,{}],'
                    '["$","div",null,{"children":["$L5","$L6","$L7"]}]]}]'
                ),
                (
                    '1:["$","a",null,{"url":'
                    '"https://www.linkedin.com/in/mohit/","children":["Mohit"]}]'
                ),
                '2:["$","p",null,{"children":["Software Engineer"]}]',
                (
                    '3:["$","p",null,{"children":'
                    '["March 18, 2025, Raj was Mohit’s mentor"]}]'
                ),
                '4:["$","p",null,{"children":["Excellent engineer."]}]',
                (
                    '5:["$","a",null,{"url":'
                    '"https://www.linkedin.com/in/ayushi/","children":["Ayushi"]}]'
                ),
                (
                    '6:["$","p",null,{"children":'
                    '["April 25, 2021, Raj was Ayushi’s client"]}]'
                ),
                '7:["$","p",null,{"children":["Creative and dedicated."]}]',
            ]
        )
    )

    assert [
        item.model_dump()
        for item in extract_recommendations_from_flight(document, "given")
    ] == [
        {
            "type": "given",
            "person_name": "Mohit",
            "person_profile_url": "https://www.linkedin.com/in/mohit/",
            "headline": "Software Engineer",
            "date": "March 18, 2025",
            "relationship": "Raj was Mohit’s mentor",
            "text": "Excellent engineer.",
        },
        {
            "type": "given",
            "person_name": "Ayushi",
            "person_profile_url": "https://www.linkedin.com/in/ayushi/",
            "headline": None,
            "date": "April 25, 2021",
            "relationship": "Raj was Ayushi’s client",
            "text": "Creative and dedicated.",
        },
    ]


@pytest.mark.parametrize(
    ("pager_id", "extractor"),
    [
        (
            "com.linkedin.sdui.pagers.profile.details.publications",
            extract_publications_pagination_request,
        ),
        (
            "com.linkedin.sdui.pagers.profile.details.testscores",
            extract_test_scores_pagination_request,
        ),
    ],
)
def test_extract_new_section_pagination_request(pager_id, extractor) -> None:
    raw_request = {
        "$type": "proto.sdui.actions.requests.PaginationRequest",
        "pagerId": pager_id,
        "requestedArguments": {
            "payload": {"vanityName": "ada", "start": 0, "count": 10}
        },
    }
    document = parse_flight_stream(
        flight_stream([f"0:{json.dumps(raw_request)}"])
    )

    assert extractor(document) == SduiPaginationRequest(
        pager_id=pager_id,
        requested_arguments=raw_request["requestedArguments"],
        raw_request=raw_request,
    )


def test_extract_received_and_given_recommendation_pagers() -> None:
    requests = []
    for recommendation_type in ("Received", "Given"):
        requests.append(
            {
                "$type": "proto.sdui.actions.requests.PaginationRequest",
                "pagerId": "com.linkedin.sdui.pagers.profile.details.recommendations",
                "requestedArguments": {
                    "payload": {
                        "type": recommendation_type,
                        "vanityName": "ada",
                        "start": 0,
                        "count": 15,
                    }
                },
            }
        )
    document = parse_flight_stream(
        flight_stream([f"{index}:{json.dumps(value)}" for index, value in enumerate(requests)])
    )

    extracted = extract_recommendations_pagination_requests(document)

    assert [request.requested_arguments["payload"]["type"] for request in extracted] == [
        "Received",
        "Given",
    ]


def test_extract_skills_details_path_from_preview_component() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","a",null,{"url":"/in/ada-lovelace/details/skills/",'
                    '"children":["Show all skills"]}]'
                )
            ]
        )
    )

    assert extract_skills_details_path(document) == "/in/ada-lovelace/details/skills/"


@pytest.mark.parametrize("section", ["experience", "projects"])
def test_extract_profile_details_path_from_preview_component(section: str) -> None:
    path = f"/in/ada-lovelace/details/{section}/"
    document = parse_flight_stream(
        flight_stream([f'0:["$","a",null,{{"url":"{path}"}}]'])
    )

    assert extract_profile_details_path(document, section) == path


def test_extract_projects_pagination_request() -> None:
    raw_request = {
        "$type": "proto.sdui.actions.requests.PaginationRequest",
        "pagerId": "com.linkedin.sdui.pagers.profile.details.projects",
        "requestedArguments": {
            "payload": {
                "vanityName": "ada-lovelace",
                "profileId": "member-id",
                "start": 0,
                "count": 10,
            },
            "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
        },
    }
    document = parse_flight_stream(
        flight_stream([f"0:{json.dumps(raw_request)}"])
    )

    request = extract_projects_pagination_request(document)

    assert request == SduiPaginationRequest(
        pager_id="com.linkedin.sdui.pagers.profile.details.projects",
        requested_arguments=raw_request["requestedArguments"],
        raw_request=raw_request,
    )


def test_extract_projects_from_divided_sdui_rows() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","div",null,{"children":['
                    '["$","div",null,{"children":["$L1","$L2","$L3","$L4"]}],'
                    '["$","hr",null,{}],'
                    '["$","div",null,{"children":["$L5","$L6"]}]'
                    "]}]"
                ),
                '1:["$","p",null,{"children":["Analytical Engine"]}]',
                (
                    '2:["$","p",null,{"children":'
                    '["Built a general-purpose mechanical computer."]}]'
                ),
                (
                    '3:["$","button",null,{"children":'
                    '[["$","span",null,{"children":["Skills:"]}],'
                    '["$","span",null,{"children":["Mathematics, Algorithms"]}]]}]'
                ),
                (
                    '4:["$","a",null,{"url":"https://example.com/engine",'
                    '"children":["Project link"]}]'
                ),
                '5:["$","p",null,{"children":["Notes on Bernoulli numbers"]}]',
                (
                    '6:["$","p",null,{"children":'
                    '["Jan 1842 - Dec 1843"]}]'
                ),
            ]
        )
    )

    assert [item.model_dump() for item in extract_projects_from_flight(document)] == [
        {
            "title": "Analytical Engine",
            "description": "Built a general-purpose mechanical computer.",
            "url": "https://example.com/engine",
            "skills": ["Mathematics", "Algorithms"],
            "start_date": None,
            "end_date": None,
        },
        {
            "title": "Notes on Bernoulli numbers",
            "description": None,
            "url": None,
            "skills": [],
            "start_date": {"year": 1842, "month": 1},
            "end_date": {"year": 1843, "month": 12},
        },
    ]


def test_extract_skills_pagination_request_selects_all_filter() -> None:
    other_request = {
        "$type": "proto.sdui.actions.requests.PaginationRequest",
        "pagerId": "com.linkedin.sdui.pagers.profile.details.skills",
        "requestedArguments": {
            "payload": {
                "vanityName": "ada-lovelace",
                "start": 0,
                "count": 10,
                "filter": "ProfileSkillCategory_INTERPERSONAL",
            }
        },
    }
    all_request = {
        "$type": "proto.sdui.actions.requests.PaginationRequest",
        "pagerId": "com.linkedin.sdui.pagers.profile.details.skills",
        "requestedArguments": {
            "$type": "proto.sdui.actions.requests.RequestedArguments",
            "payload": {
                "vanityName": "ada-lovelace",
                "profileId": "member-id",
                "start": 0,
                "count": 10,
                "filter": "ProfileSkillCategory_ALL",
            },
            "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
        },
    }
    document = parse_flight_stream(
        flight_stream([f"0:{json.dumps([other_request, all_request])}"])
    )

    request = extract_skills_pagination_request(document)

    assert request == SduiPaginationRequest(
        pager_id="com.linkedin.sdui.pagers.profile.details.skills",
        requested_arguments=all_request["requestedArguments"],
        raw_request=all_request,
    )
    assert request.client_arguments(
        "com.linkedin.sdui.flagshipnav.profile.ProfileSkillDetails"
    ) == {
        **all_request["requestedArguments"],
        "states": [],
        "screenId": "com.linkedin.sdui.flagshipnav.profile.ProfileSkillDetails",
    }


def test_extract_about_from_component_flight_stream() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","section",null,{"componentKey":'
                    '"com.linkedin.sdui.profile.card.memberAbout",'
                    '"children":["$L1","$L2","$L3"]}]'
                ),
                '1:["$","h2",null,{"children":["About"]}]',
                '2:["$","p",null,{"children":["First paragraph."]}]',
                '3:["$","p",null,{"children":["Second paragraph."]}]',
            ]
        )
    )

    assert extract_about_from_flight(document) == (
        "First paragraph.\n\nSecond paragraph."
    )


def test_extract_about_from_deferred_component_content() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","div",null,{"children":'
                    '[["$","section",null,{"componentKey":'
                    '"com.linkedin.sdui.profile.card.memberAbout",'
                    '"initialContent":"$L1"}]]}]'
                ),
                (
                    '1:["$","div",null,{"children":'
                    '["$L2","$L3"]}]'
                ),
                '2:["$","h2",null,{"children":["About"]}]',
                '3:["$","p",null,{"children":["Deferred summary"]}]',
            ]
        )
    )

    assert extract_about_from_flight(document) == "Deferred summary"


def test_extract_certifications_from_component_flight_stream() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","section",null,{"componentKey":'
                    '"com.linkedin.sdui.profile.card.memberCertificationTopLevel",'
                    '"children":["$L1","$L8"]}]'
                ),
                (
                    '1:["$","div",null,{"children":'
                    '["$L2","$L3","$L4","$L5","$L6","$L7"]}]'
                ),
                (
                    '2:["$","img",null,{"url":'
                    '"https://www.linkedin.com/company/123/"}]'
                ),
                '3:["$","span",null,{"children":["Cloud Engineer"]}]',
                '4:["$","span",null,{"children":["Example Authority"]}]',
                (
                    '5:["$","span",null,{"children":'
                    '["Issued Jan 2024 · Expires Jan 2027"]}]'
                ),
                (
                    '6:["$","span",null,{"children":'
                    '["Credential ID ABC-123"]}]'
                ),
                (
                    '7:["$","a",null,{"url":'
                    '"https://www.linkedin.com/safety/go/?url='
                    'https%3A%2F%2Fcredentials.example%2FABC-123",'
                    '"children":["Show credential"]}]'
                ),
                (
                    '8:["$","div",null,{"children":'
                    '["$L9","$La","$Lb"]}]'
                ),
                (
                    '9:["$","img",null,{"url":'
                    '"https://www.linkedin.com/company/456/",'
                    '"renderPayload":{"rootUrl":'
                    '"https://media.licdn.com/company-logo_"}}]'
                ),
                'a:["$","span",null,{"children":["Security Basics"]}]',
                'b:["$","span",null,{"children":["Second Authority"]}]',
            ]
        )
    )

    assert [item.model_dump() for item in extract_certifications_from_flight(document)] == [
        {
            "name": "Cloud Engineer",
            "authority": "Example Authority",
            "license_number": "ABC-123",
            "url": "https://credentials.example/ABC-123",
            "start_date": {"year": 2024, "month": 1},
            "end_date": {"year": 2027, "month": 1},
        },
        {
            "name": "Security Basics",
            "authority": "Second Authority",
            "license_number": None,
            "url": None,
            "start_date": None,
            "end_date": None,
        },
    ]


def test_extract_courses_and_pager_from_detail_flight_stream() -> None:
    next_request = {
        "$type": "proto.sdui.actions.requests.PaginationRequest",
        "pagerId": "com.linkedin.sdui.pagers.profile.details.courses",
        "requestedArguments": {
            "payload": {
                "vanityName": "ada-lovelace",
                "profileId": "member-id",
                "start": 10,
                "count": 10,
            }
        },
    }
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","div",null,{"componentKey":'
                    '"com.linkedin.sdui.profile.CourseDetails",'
                    '"children":["$L1","$L2"]}]'
                ),
                '1:["$","span",null,{"children":["Operating Systems"]}]',
                '2:["$","span",null,{"children":["CS F372"]}]',
                f"3:{json.dumps(next_request)}",
            ]
        )
    )

    assert [item.model_dump() for item in extract_courses_from_flight(document)] == [
        {
            "name": "Operating Systems",
            "number": "CS F372",
            "associated_with": None,
        }
    ]
    assert extract_courses_pagination_request(document) == SduiPaginationRequest(
        pager_id="com.linkedin.sdui.pagers.profile.details.courses",
        requested_arguments=next_request["requestedArguments"],
        raw_request=next_request,
    )


def test_extract_courses_ignores_empty_state() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","div",null,{"children":'
                    '["Nothing to see for now",'
                    '"Courses that Ada adds will appear here."]}]'
                )
            ]
        )
    )

    assert extract_courses_from_flight(document) == []


def test_extract_course_association_from_semantic_preview_card() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","div",null,{"children":'
                    '[["$","section",null,{"componentKey":'
                    '"com.linkedin.sdui.profile.card.memberCourseTopLevelSection",'
                    '"initialContent":"$L1"}]]}]'
                ),
                '1:["$","div",null,{"initialContent":["$L2","$L3"]}]',
                (
                    '2:["$","span",null,{"children":'
                    '["Executive Program in Business Management"]}]'
                ),
                (
                    '3:["$","span",null,{"children":'
                    '["Associated with Indian Institute of Management, Calcutta"]}]'
                ),
            ]
        )
    )

    assert [item.model_dump() for item in extract_courses_from_flight(document)] == [
        {
            "name": "Executive Program in Business Management",
            "number": None,
            "associated_with": "Indian Institute of Management, Calcutta",
        }
    ]


def test_extract_languages_from_component_flight_stream() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","section",null,{"componentKey":'
                    '"com.linkedin.sdui.profile.card.memberLanguageTopLevel",'
                    '"children":["$L1","$L4","$L7"]}]'
                ),
                '1:["$","div",null,{"children":["$L2","$L3"]}]',
                '2:["$","span",null,{"children":["English"]}]',
                (
                    '3:["$","span",null,{"children":'
                    '["Full professional proficiency"]}]'
                ),
                '4:["$","div",null,{"children":["$L5","$L6"]}]',
                '5:["$","span",null,{"children":["Hindi"]}]',
                (
                    '6:["$","span",null,{"children":'
                    '["Native or bilingual proficiency"]}]'
                ),
                '7:["$","div",null,{"children":["$L8","$L9"]}]',
                '8:["$","span",null,{"children":["French"]}]',
                (
                    '9:["$","span",null,{"children":'
                    '["Advanced professional proficiency"]}]'
                ),
            ]
        )
    )

    assert [item.model_dump() for item in extract_languages_from_flight(document)] == [
        {"name": "English", "proficiency": "Full professional proficiency"},
        {"name": "Hindi", "proficiency": "Native or bilingual proficiency"},
        {"name": "French", "proficiency": "Advanced professional proficiency"},
    ]


def test_extract_languages_from_deferred_component_rows() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","div",null,{"children":'
                    '[["$","section",null,{"componentKey":'
                    '"com.linkedin.sdui.profile.card.memberLanguageTopLevel",'
                    '"initialContent":["$L1","$L4"]}]]}]'
                ),
                '1:["$","div",null,{"initialContent":["$L2","$L3"]}]',
                '2:["$","span",null,{"children":["English"]}]',
                (
                    '3:["$","span",null,{"children":'
                    '["Professional working proficiency"]}]'
                ),
                '4:["$","div",null,{"initialContent":["$L5","$L6"]}]',
                '5:["$","span",null,{"children":["Hindi"]}]',
                (
                    '6:["$","span",null,{"children":'
                    '["Native or bilingual proficiency"]}]'
                ),
            ]
        )
    )

    assert [item.model_dump() for item in extract_languages_from_flight(document)] == [
        {"name": "English", "proficiency": "Professional working proficiency"},
        {"name": "Hindi", "proficiency": "Native or bilingual proficiency"},
    ]


def test_extract_projects_from_deferred_rows() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","section",null,{"componentKey":'
                    '"com.linkedin.sdui.profile.card.memberProjects",'
                    '"initialContent":["$L1","$L4","$L5"]}]'
                ),
                '1:["$","div",null,{"initialContent":["$L2","$L3"]}]',
                '2:["$","span",null,{"children":["Password Generator"]}]',
                '3:["$","p",null,{"children":["Built with JavaScript"]}]',
                '4:["$","hr",null,{}]',
                '5:["$","div",null,{"initialContent":"$L6"}]',
                '6:["$","span",null,{"children":["Simon Game"]}]',
            ]
        )
    )

    assert [item.title for item in extract_projects_from_flight(document)] == [
        "Password Generator",
        "Simon Game",
    ]


def test_extract_languages_without_proficiency_from_divided_rows() -> None:
    document = parse_flight_stream(
        flight_stream(
            [
                (
                    '0:["$","section",null,{"componentKey":'
                    '"com.linkedin.sdui.profile.card.memberLanguageTopLevel",'
                    '"children":"$L1"}]'
                ),
                '1:["$","div",null,{"children":["$L2","$L3","$L4"]}]',
                '2:["$","div",null,{"children":["English"]}]',
                '3:["$","hr",null,{"role":"separator"}]',
                '4:["$","div",null,{"children":["Spanish"]}]',
            ]
        )
    )

    assert [item.model_dump() for item in extract_languages_from_flight(document)] == [
        {"name": "English", "proficiency": None},
        {"name": "Spanish", "proficiency": None},
    ]


def test_extract_profile_from_top_card_component() -> None:
    html = hydration_html(
        [
            (
                '0:["$","main",null,{"observabilityIdentifier":'
                '"com.linkedin.sdui.impl.profile.components.topCard",'
                '"children":{"initialContent":"$L1"}}]'
            ),
            (
                '1:["$","section",null,{"children":["$L2","$L3","$La","$L4",'
                '"$L5","$L8"]}]'
            ),
            (
                '2:["$","div",null,{"requestedArguments":{"payload":'
                '{"givenName":"Ada","familyName":"Lovelace"}}}]'
            ),
            '3:["$","h2",null,{"children":["Ada Lovelace"]}]',
            '4:["$","p",null,{"children":["Mathematician and programmer"]}]',
            (
                '5:["$","div",null,{"children":'
                '["Unrelated label","$L6","·","$L7"]}]'
            ),
            (
                '6:["$","span",null,{"textProps":{"children":'
                '["London, England"]}}]'
            ),
            (
                '7:["$","div",null,{"children":"$Lb"}]'
            ),
            (
                'b:["$","a",null,{"url":"/in/ada-lovelace/overlay/contact-info/",'
                '"children":["Contact info"]}]'
            ),
            (
                '8:["$","div",null,{"aria-label":"Profile photo",'
                '"children":"$L9"}]'
            ),
            (
                '9:["$","img",null,{"renderPayload":{'
                '"rootUrl":"https://media.example/profile_",'
                '"imageRenditions":[{"width":100,"height":100,'
                '"suffixUrl":"100.jpg"},{"width":400,"height":400,'
                '"suffixUrl":"400.jpg"}]}}]'
            ),
            'a:["$","p",null,{"children":["View Ada’s verifications"]}]',
        ]
    )

    profile = extract_profile_from_como(html, "ada-lovelace")

    assert profile.model_dump() == {
        "public_identifier": "ada-lovelace",
        "profile_url": "https://www.linkedin.com/in/ada-lovelace/",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "full_name": "Ada Lovelace",
        "headline": "Mathematician and programmer",
        "location": "London, England",
        "about": None,
        "experiences": [],
        "education": [],
        "skills": [],
        "projects": [],
        "test_scores": [],
        "publications": [],
            "recommendations": [],
            "certifications": [],
            "courses": [],
            "languages": [],
        "has_profile_photo_frame": False,
        "profile_images": [
            {
                "url": "https://media.example/profile_100.jpg",
                "width": 100,
                "height": 100,
            },
            {
                "url": "https://media.example/profile_400.jpg",
                "width": 400,
                "height": 400,
            },
        ],
    }


def test_extract_profile_detects_generic_profile_photo_frame() -> None:
    html = hydration_html(
        [
            (
                '0:["$","main",null,{"observabilityIdentifier":'
                '"com.linkedin.sdui.impl.profile.components.topCard",'
                '"children":{"initialContent":"$L1"}}]'
            ),
            (
                '1:["$","section",null,{"requestedArguments":{"payload":'
                '{"givenName":"Ada","familyName":"Lovelace"}},'
                '"children":["$L2","$L3","$L4"]}]'
            ),
            '2:["$","h2",null,{"children":["Ada Lovelace"]}]',
            '3:["$","p",null,{"children":["Mathematician"]}]',
            (
                '4:["$","img",null,{"aria-label":"Profile photo","renderPayload":{'
                '"rootUrl":"https://media.example/profile-framedphoto-shrink_",'
                '"imageRenditions":[{"width":400,"height":400,'
                '"suffixUrl":"400.jpg"}]}}]'
            ),
        ]
    )

    profile = extract_profile_from_como(html, "ada-lovelace")

    assert profile.has_profile_photo_frame is True


@pytest.mark.parametrize(
    "html",
    [
        "<html></html>",
        "<script>window.__como_rehydration__ = not_json;</script>",
        hydration_html(["not-a-flight-record"]),
    ],
)
def test_parse_como_flight_rejects_invalid_documents(html: str) -> None:
    with pytest.raises(ComoFlightParseError):
        parse_como_flight(html)


@pytest.mark.asyncio
async def test_ssr_client_fetches_and_normalizes_profile_page() -> None:
    html = hydration_html(
        [
            (
                '0:["$","main",null,{"observabilityIdentifier":'
                '"com.linkedin.sdui.impl.profile.components.topCard",'
                '"children":{"initialContent":"$L1"}}]'
            ),
            (
                '1:["$","section",null,{"requestedArguments":{"payload":'
                '{"givenName":"Ada","familyName":"Lovelace"}},'
                '"requests":[{"$type":"proto.sdui.actions.core.AsyncComponentRequest",'
                '"newComponentId":"com.linkedin.profileCardsAboveActivity",'
                '"requestedArguments":{}},{"$type":'
                '"proto.sdui.actions.core.AsyncComponentRequest",'
                '"newComponentId":"com.linkedin.profileCardsExperienceOnly",'
                '"requestedArguments":{"payload":{"vanityName":"ada-lovelace"},'
                '"requestMetadata":{}}},{"$type":'
                '"proto.sdui.actions.core.AsyncComponentRequest",'
                '"newComponentId":"com.linkedin.profileCardsBelowActivityPart1WithoutExp",'
                '"requestedArguments":{}},{"$type":'
                '"proto.sdui.actions.core.AsyncComponentRequest",'
                '"newComponentId":"com.linkedin.profileCardsBelowActivityPart4",'
                '"requestedArguments":{}},{"$type":'
                '"proto.sdui.actions.core.AsyncComponentRequest",'
                '"newComponentId":"com.linkedin.profileCardsBelowActivityPart7",'
                '"requestedArguments":{}}],"children":["$L2","$L3"]}]'
            ),
            '2:["$","h2",null,{"children":["Ada Lovelace"]}]',
            '3:["$","p",null,{"children":["Mathematician"]}]',
        ]
    )

    class FakeTransport:
        def __init__(self) -> None:
            self.identifiers: list[str] = []

        async def fetch_profile_page(self, public_identifier: str) -> ProfilePageDocument:
            self.identifiers.append(public_identifier)
            return ProfilePageDocument(html, "text/html", len(html.encode()))

    class FakeComponentTransport:
        def __init__(self) -> None:
            self.component_ids: list[str] = []

        async def fetch_component(
            self,
            request: SduiComponentRequest,
        ) -> ComoFlightDocument:
            self.component_ids.append(request.component_id)
            if request.component_id.endswith("profileCardsAboveActivity"):
                return parse_flight_stream(
                    flight_stream(
                        [
                            (
                                '0:["$","section",null,{"componentKey":'
                                '"com.linkedin.profile.memberAbout",'
                                '"children":["$L1","$L2"]}]'
                            ),
                            '1:["$","h2",null,{"children":["About"]}]',
                            '2:["$","p",null,{"children":["Profile summary"]}]',
                        ]
                    )
                )
            if request.component_id.endswith("profileCardsBelowActivityPart1WithoutExp"):
                return parse_flight_stream(
                    flight_stream(
                        [
                            (
                                '0:["$","button",null,{"url":'
                                '"https://www.linkedin.com/school/456/",'
                                '"children":["$L1","$L2","$L3"]}]'
                            ),
                            '1:["$","span",null,{"children":["Example University"]}]',
                            '2:["$","span",null,{"children":["MSc, Computing"]}]',
                            '3:["$","span",null,{"children":["2022 – 2024"]}]',
                            (
                                '4:["$","section",null,{"componentKey":'
                                '"com.linkedin.profile.memberCertificationTopLevel",'
                                '"children":"$L5"}]'
                            ),
                            (
                                '5:["$","div",null,{"children":'
                                '["$L6","$L7","$L8"]}]'
                            ),
                            (
                                '6:["$","img",null,{"url":'
                                '"https://www.linkedin.com/company/789/"}]'
                            ),
                            '7:["$","span",null,{"children":["Computing"]}]',
                            '8:["$","span",null,{"children":["Example Org"]}]',
                        ]
                    )
                )
            if request.component_id.endswith("profileCardsBelowActivityPart4"):
                return parse_flight_stream(
                    flight_stream(
                        [
                            (
                                '0:["$","section",null,{"componentKey":'
                                '"com.linkedin.profile.memberLanguageTopLevel",'
                                '"children":"$L1"}]'
                            ),
                            '1:["$","div",null,{"children":["$L2","$L3"]}]',
                            '2:["$","span",null,{"children":["English"]}]',
                            (
                                '3:["$","span",null,{"children":'
                                '["Full professional proficiency"]}]'
                            ),
                        ]
                    )
                )
            if request.component_id.endswith("profileCardsBelowActivityPart7"):
                return parse_flight_stream(
                    flight_stream(
                        [
                            (
                                '0:["$","div",null,{"componentKey":'
                                '"com.linkedin.sdui.profile.skill(member, 1)",'
                                '"children":["$L1"]}]'
                            ),
                            '1:["$","span",null,{"children":["Python"]}]',
                        ]
                    )
                )
            return parse_flight_stream(
                flight_stream(
                    [
                        (
                            '0:["$","button",null,{"url":'
                            '"https://www.linkedin.com/company/123/",'
                            '"children":["$L1","$L2","$L3"]}]'
                        ),
                        '1:["$","span",null,{"children":["Engineer"]}]',
                        '2:["$","span",null,{"children":["Example Corp"]}]',
                        '3:["$","span",null,{"children":["Jan 2024 - Present"]}]',
                    ]
                )
            )

    transport = FakeTransport()
    component_transport = FakeComponentTransport()
    client = SsrLinkedInProfileClient(transport, component_transport)

    profile = await client.fetch_profile(
        ProfileRequest(profile_url="https://www.linkedin.com/in/ada-lovelace/")
    )

    assert transport.identifiers == ["ada-lovelace"]
    assert profile.full_name == "Ada Lovelace"
    assert profile.headline == "Mathematician"
    assert profile.about == "Profile summary"
    assert [position.title for position in profile.experiences] == ["Engineer"]
    assert [item.school_name for item in profile.education] == ["Example University"]
    assert [item.name for item in profile.certifications] == ["Computing"]
    assert [item.name for item in profile.languages] == ["English"]
    assert profile.skills == ["Python"]
    assert component_transport.component_ids == [
        "com.linkedin.profileCardsAboveActivity",
        "com.linkedin.profileCardsExperienceOnly",
        "com.linkedin.profileCardsBelowActivityPart1WithoutExp",
        "com.linkedin.profileCardsBelowActivityPart4",
        "com.linkedin.profileCardsBelowActivityPart7",
    ]


@pytest.mark.asyncio
async def test_ssr_client_fetches_full_publications_scores_and_recommendations() -> None:
    profile_html = hydration_html(
        [
            (
                '0:["$","main",null,{"observabilityIdentifier":'
                '"com.linkedin.sdui.impl.profile.components.topCard",'
                '"children":{"initialContent":"$L1"}}]'
            ),
            (
                '1:["$","section",null,{"requestedArguments":{"payload":'
                '{"givenName":"Ada","familyName":"Lovelace"}},'
                '"requests":[{"$type":"proto.sdui.actions.core.AsyncComponentRequest",'
                '"newComponentId":"com.linkedin.profileCardsBelowActivityPart2",'
                '"requestedArguments":{}},{"$type":'
                '"proto.sdui.actions.core.AsyncComponentRequest",'
                '"newComponentId":"com.linkedin.profileCardsBelowActivityPart3",'
                '"requestedArguments":{}}]}]'
            ),
        ]
    )

    def pager(pager_id: str, payload: dict[str, object]) -> dict[str, object]:
        return {
            "$type": "proto.sdui.actions.requests.PaginationRequest",
            "pagerId": pager_id,
            "requestedArguments": {"payload": payload},
        }

    detail_requests = {
        "publications": [
            pager(
                "com.linkedin.sdui.pagers.profile.details.publications",
                {"vanityName": "ada-lovelace", "start": 0, "count": 10},
            )
        ],
        "test-scores": [
            pager(
                "com.linkedin.sdui.pagers.profile.details.testscores",
                {"vanityName": "ada-lovelace", "start": 0, "count": 10},
            )
        ],
        "recommendations": [
            pager(
                "com.linkedin.sdui.pagers.profile.details.recommendations",
                {
                    "type": recommendation_type,
                    "vanityName": "ada-lovelace",
                    "start": 0,
                    "count": 15,
                },
            )
            for recommendation_type in ("Received", "Given")
        ],
    }

    class FakeTransport:
        def __init__(self) -> None:
            self.details_calls: list[tuple[str, str]] = []

        async def fetch_profile_page(self, public_identifier: str) -> ProfilePageDocument:
            del public_identifier
            return ProfilePageDocument(profile_html, "text/html", len(profile_html))

        async def fetch_profile_details_page(
            self,
            public_identifier: str,
            section: str,
        ) -> ProfilePageDocument:
            self.details_calls.append((public_identifier, section))
            if section not in detail_requests:
                html = hydration_html(['0:["$","div",null,{"children":[]}]'])
                return ProfilePageDocument(html, "text/html", len(html))
            records = [
                f"{index}:{json.dumps(value)}"
                for index, value in enumerate(detail_requests[section])
            ]
            html = hydration_html(records)
            return ProfilePageDocument(html, "text/html", len(html))

    class FakeComponentTransport:
        async def fetch_component(
            self,
            request: SduiComponentRequest,
        ) -> ComoFlightDocument:
            if request.component_id.endswith("Part2"):
                return parse_flight_stream(
                    flight_stream(
                        [
                            (
                                '0:["$","div",null,{"children":['
                                '"Recommendations","$L1","$L2","$L3"]}]'
                            ),
                            (
                                '1:["$","a",null,{"url":'
                                '"https://www.linkedin.com/in/grace/",'
                                '"children":["Grace"]}]'
                            ),
                            (
                                '2:["$","p",null,{"children":'
                                '["March 1, 2024, Grace managed Ada"]}]'
                            ),
                            '3:["$","p",null,{"children":["Excellent work."]}]',
                        ]
                    )
                )
            return parse_flight_stream(
                flight_stream(
                    [
                        (
                            '0:["$","div",null,{"children":['
                            '"/in/ada-lovelace/details/publications/",'
                            '"/in/ada-lovelace/details/test-scores/"]}]'
                        )
                    ]
                )
            )

    class FakePaginationTransport:
        async def fetch_page(
            self,
            request: SduiPaginationRequest,
            screen_id: str,
        ) -> ComoFlightDocument:
            if request.pager_id.endswith(".publications"):
                assert screen_id.endswith(".ProfilePublicationDetails")
                return parse_flight_stream(
                    flight_stream(
                        [
                            (
                                '0:["$","div",null,{"children":['
                                '"$L1","$L2","$L3"]}]'
                            ),
                            '1:["$","p",null,{"children":["Paper"]}]',
                            (
                                '2:["$","p",null,{"children":'
                                '["Journal · Jun 12, 2020"]}]'
                            ),
                            '3:["$","p",null,{"children":["Description"]}]',
                        ]
                    )
                )
            if request.pager_id.endswith(".testscores"):
                assert screen_id.endswith(".ProfileTestScoreDetails")
                return parse_flight_stream(
                    flight_stream(
                        [
                            '0:["$","div",null,{"children":["$L1","$L2"]}]',
                            '1:["$","p",null,{"children":["GATE"]}]',
                            '2:["$","p",null,{"children":["Score: 700 · Feb 2021"]}]',
                        ]
                    )
                )
            assert screen_id.endswith(".ProfileRecommendationDetails")
            recommendation_type = request.requested_arguments["payload"]["type"]
            if recommendation_type == "Given":
                return parse_flight_stream(
                    '0:["$","div",null,{"children":[]}]\n'
                )
            return parse_flight_stream(
                flight_stream(
                    [
                        '0:["$","div",null,{"children":["$L1","$L2","$L3"]}]',
                        (
                            '1:["$","a",null,{"url":'
                            '"https://www.linkedin.com/in/grace/",'
                            '"children":["Grace"]}]'
                        ),
                        (
                            '2:["$","p",null,{"children":'
                            '["March 1, 2024, Grace managed Ada"]}]'
                        ),
                        '3:["$","p",null,{"children":["Excellent work."]}]',
                    ]
                )
            )

    transport = FakeTransport()
    client = SsrLinkedInProfileClient(
        transport,
        FakeComponentTransport(),
        transport,
        FakePaginationTransport(),
    )

    profile = await client.fetch_profile(
        ProfileRequest(profile_url="https://www.linkedin.com/in/ada-lovelace/")
    )

    assert [item.title for item in profile.publications] == ["Paper"]
    assert [item.name for item in profile.test_scores] == ["GATE"]
    assert [item.person_name for item in profile.recommendations] == ["Grace"]
    assert transport.details_calls[:3] == [
        ("ada-lovelace", "recommendations"),
        ("ada-lovelace", "publications"),
        ("ada-lovelace", "test-scores"),
    ]


@pytest.mark.asyncio
async def test_ssr_client_replaces_certification_and_course_previews_with_all_pages() -> None:
    profile_html = hydration_html(
        [
            (
                '0:["$","main",null,{"observabilityIdentifier":'
                '"com.linkedin.sdui.impl.profile.components.topCard",'
                '"children":{"initialContent":"$L1"}}]'
            ),
            (
                '1:["$","section",null,{"requestedArguments":{"payload":'
                '{"givenName":"Ada","familyName":"Lovelace"}},'
                '"requests":[{"$type":"proto.sdui.actions.core.AsyncComponentRequest",'
                '"newComponentId":"com.linkedin.profileCardsBelowActivityPart1WithoutExp",'
                '"requestedArguments":{}},{"$type":'
                '"proto.sdui.actions.core.AsyncComponentRequest",'
                '"newComponentId":"com.linkedin.profileCardsBelowActivityPart3",'
                '"requestedArguments":{}}]}]'
            ),
        ]
    )

    def pager(section: str, start: int) -> dict[str, object]:
        return {
            "$type": "proto.sdui.actions.requests.PaginationRequest",
            "pagerId": f"com.linkedin.sdui.pagers.profile.details.{section}",
            "requestedArguments": {
                "payload": {
                    "vanityName": "ada-lovelace",
                    "profileId": "member-id",
                    "start": start,
                    "count": 1,
                }
            },
        }

    detail_html = {
        section: hydration_html([f"0:{json.dumps(pager(section, 0))}"])
        for section in ("certifications", "courses")
    }

    class FakeTransport:
        def __init__(self) -> None:
            self.details_calls: list[tuple[str, str]] = []

        async def fetch_profile_page(self, public_identifier: str) -> ProfilePageDocument:
            del public_identifier
            return ProfilePageDocument(profile_html, "text/html", len(profile_html))

        async def fetch_profile_details_page(
            self,
            public_identifier: str,
            section: str,
        ) -> ProfilePageDocument:
            self.details_calls.append((public_identifier, section))
            html = detail_html.get(
                section,
                hydration_html(['0:["$","div",null,{"children":[]}]']),
            )
            return ProfilePageDocument(html, "text/html", len(html))

    class FakeComponentTransport:
        async def fetch_component(
            self,
            request: SduiComponentRequest,
        ) -> ComoFlightDocument:
            section = (
                "certifications"
                if request.component_id.endswith("Part1WithoutExp")
                else "courses"
            )
            return parse_flight_stream(
                flight_stream(
                    [
                        (
                            '0:["$","a",null,{"url":'
                            f'"/in/ada-lovelace/details/{section}/",'
                            f'"children":["Show all {section}"]}}]'
                        )
                    ]
                )
            )

    class FakePaginationTransport:
        async def fetch_page(
            self,
            request: SduiPaginationRequest,
            screen_id: str,
        ) -> ComoFlightDocument:
            payload = request.payload()
            assert payload is not None
            start = payload["start"]
            assert isinstance(start, int)
            section = request.pager_id.rsplit(".", maxsplit=1)[-1]
            if start >= 2:
                return parse_flight_stream(
                    flight_stream(['0:["$","div",null,{"children":[]}]'])
                )
            records: list[str]
            if section == "certifications":
                assert screen_id.endswith(".ProfileCertificationDetails")
                records = [
                    (
                        '0:["$","div",null,{"componentKey":'
                        '"com.linkedin.sdui.profile.CertificationDetails",'
                        '"children":["$L1","$L2","$L3"]}]'
                    ),
                    (
                        '1:["$","img",null,{"url":'
                        f'"https://www.linkedin.com/company/{start + 1}/"}}]'
                    ),
                    f'2:["$","span",null,{{"children":["Certificate {start + 1}"]}}]',
                    '3:["$","span",null,{"children":["Authority"]}]',
                ]
            else:
                assert screen_id.endswith(".ProfileCourseDetails")
                records = [
                    (
                        '0:["$","div",null,{"componentKey":'
                        '"com.linkedin.sdui.profile.CourseDetails",'
                        '"children":["$L1","$L2"]}]'
                    ),
                    f'1:["$","span",null,{{"children":["Course {start + 1}"]}}]',
                    f'2:["$","span",null,{{"children":["CODE {start + 1}"]}}]',
                ]
            return parse_flight_stream(flight_stream(records))

    transport = FakeTransport()
    client = SsrLinkedInProfileClient(
        transport,
        FakeComponentTransport(),
        transport,
        FakePaginationTransport(),
    )

    profile = await client.fetch_profile(
        ProfileRequest(profile_url="https://www.linkedin.com/in/ada-lovelace/")
    )

    assert [item.name for item in profile.certifications] == [
        "Certificate 1",
        "Certificate 2",
    ]
    assert [item.name for item in profile.courses] == ["Course 1", "Course 2"]
    assert ("ada-lovelace", "certifications") in transport.details_calls
    assert ("ada-lovelace", "courses") in transport.details_calls


@pytest.mark.asyncio
async def test_ssr_client_preserves_projects_when_preview_has_no_detail_link() -> None:
    profile_html = hydration_html(
        [
            (
                '0:["$","main",null,{"observabilityIdentifier":'
                '"com.linkedin.sdui.impl.profile.components.topCard",'
                '"children":{"initialContent":"$L1"}}]'
            ),
            (
                '1:["$","section",null,{"requestedArguments":{"payload":'
                '{"givenName":"Ada","familyName":"Lovelace"}},'
                '"requests":[{"$type":"proto.sdui.actions.core.AsyncComponentRequest",'
                '"newComponentId":'
                '"com.linkedin.profileCardsBelowActivityPart1WithoutExp",'
                '"requestedArguments":{}}]}]'
            ),
        ]
    )

    class FakeTransport:
        async def fetch_profile_page(self, public_identifier: str) -> ProfilePageDocument:
            del public_identifier
            return ProfilePageDocument(profile_html, "text/html", len(profile_html))

    class FakeComponentTransport:
        async def fetch_component(
            self,
            request: SduiComponentRequest,
        ) -> ComoFlightDocument:
            del request
            return parse_flight_stream(
                flight_stream(
                    [
                        (
                            '0:["$","section",null,{"componentKey":'
                            '"com.linkedin.sdui.profile.card.memberProjects",'
                            '"initialContent":"$L1"}]'
                        ),
                        '1:["$","div",null,{"initialContent":"$L2"}]',
                        '2:["$","span",null,{"children":["Analytical Engine"]}]',
                    ]
                )
            )

    profile = await SsrLinkedInProfileClient(
        FakeTransport(),
        FakeComponentTransport(),
    ).fetch_profile(
        ProfileRequest(profile_url="https://www.linkedin.com/in/ada-lovelace/")
    )

    assert [item.title for item in profile.projects] == ["Analytical Engine"]


@pytest.mark.asyncio
async def test_ssr_client_replaces_skill_preview_with_all_paginated_skills() -> None:
    profile_html = hydration_html(
        [
            (
                '0:["$","main",null,{"observabilityIdentifier":'
                '"com.linkedin.sdui.impl.profile.components.topCard",'
                '"children":{"initialContent":"$L1"}}]'
            ),
            (
                '1:["$","section",null,{"requestedArguments":{"payload":'
                '{"givenName":"Ada","familyName":"Lovelace"}},'
                '"requests":[{"$type":"proto.sdui.actions.core.AsyncComponentRequest",'
                '"newComponentId":"com.linkedin.profileCardsBelowActivityPart7",'
                '"requestedArguments":{}}]}]'
            ),
        ]
    )
    first_request_raw = {
        "$type": "proto.sdui.actions.requests.PaginationRequest",
        "pagerId": "com.linkedin.sdui.pagers.profile.details.skills",
        "requestedArguments": {
            "payload": {
                "vanityName": "ada-lovelace",
                "profileId": "member-id",
                "start": 0,
                "count": 1,
                "filter": "ProfileSkillCategory_ALL",
            },
            "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
        },
    }
    details_html = hydration_html([f"0:{json.dumps(first_request_raw)}"])

    class FakeTransport:
        def __init__(self) -> None:
            self.details_calls: list[tuple[str, str]] = []

        async def fetch_profile_page(self, public_identifier: str) -> ProfilePageDocument:
            del public_identifier
            return ProfilePageDocument(
                profile_html,
                "text/html",
                len(profile_html.encode()),
            )

        async def fetch_profile_details_page(
            self,
            public_identifier: str,
            section: str,
        ) -> ProfilePageDocument:
            self.details_calls.append((public_identifier, section))
            return ProfilePageDocument(
                details_html,
                "text/html",
                len(details_html.encode()),
            )

    class FakeComponentTransport:
        async def fetch_component(
            self,
            request: SduiComponentRequest,
        ) -> ComoFlightDocument:
            del request
            return parse_flight_stream(
                flight_stream(
                    [
                        (
                            '0:["$","div",null,{"componentKey":'
                            '"com.linkedin.sdui.profile.skill(member, 1)",'
                            '"children":["$L1"]}]'
                        ),
                        '1:["$","span",null,{"children":["Preview skill"]}]',
                        (
                            '2:["$","a",null,{"url":'
                            '"/in/ada-lovelace/details/skills/",'
                            '"children":["Show all skills"]}]'
                        ),
                    ]
                )
            )

    class FakePaginationTransport:
        def __init__(self) -> None:
            self.starts: list[int] = []

        async def fetch_page(
            self,
            request: SduiPaginationRequest,
            screen_id: str,
        ) -> ComoFlightDocument:
            assert screen_id == (
                "com.linkedin.sdui.flagshipnav.profile.ProfileSkillDetails"
            )
            payload = request.requested_arguments["payload"]
            assert isinstance(payload, dict)
            start = payload["start"]
            assert isinstance(start, int)
            self.starts.append(start)
            if start >= 2:
                return parse_flight_stream(
                    flight_stream(['0:["$","div",null,{"children":[]}]'])
                )
            records = [
                (
                    '0:["$","div",null,{"componentKey":'
                    f'"com.linkedin.sdui.profile.skill(member, {start + 1})",'
                    '"children":["$L1"]}]'
                ),
                (
                    '1:["$","span",null,{"children":'
                    f'["{"Python" if start == 0 else "Pydantic"}"]}}]'
                ),
            ]
            return parse_flight_stream(flight_stream(records))

    transport = FakeTransport()
    pagination_transport = FakePaginationTransport()
    client = SsrLinkedInProfileClient(
        transport,
        FakeComponentTransport(),
        transport,
        pagination_transport,
    )

    profile = await client.fetch_profile(
        ProfileRequest(profile_url="https://www.linkedin.com/in/ada-lovelace/")
    )

    assert profile.skills == ["Python", "Pydantic"]
    assert transport.details_calls == [
        ("ada-lovelace", "skills"),
        ("ada-lovelace", "education"),
    ]
    assert pagination_transport.starts == [0, 1, 2]


@pytest.mark.asyncio
async def test_ssr_client_falls_back_to_detail_pages_when_preview_cards_are_missing() -> None:
    profile_html = hydration_html(
        [
            (
                '0:["$","main",null,{"observabilityIdentifier":'
                '"com.linkedin.sdui.impl.profile.components.topCard",'
                '"children":{"initialContent":"$L1"}}]'
            ),
            (
                '1:["$","section",null,{"requestedArguments":{"payload":'
                '{"givenName":"Hari","familyName":"Chintaparthi"}}}]'
            ),
        ]
    )
    education_request = {
        "$type": "proto.sdui.actions.requests.PaginationRequest",
        "pagerId": "com.linkedin.sdui.pagers.profile.details.education",
        "requestedArguments": {
            "payload": {
                "vanityName": "hari-chintaparthi",
                "profileId": "member-id",
                "start": 0,
                "count": 20,
                "detailSectionReplaceableComponentRef": "component-ref",
            }
        },
    }
    education_html = hydration_html([f"0:{json.dumps(education_request)}"])
    skills_request = {
        "$type": "proto.sdui.actions.requests.PaginationRequest",
        "pagerId": "com.linkedin.sdui.pagers.profile.details.skills",
        "requestedArguments": {
            "payload": {
                "vanityName": "hari-chintaparthi",
                "profileId": "member-id",
                "start": 0,
                "count": 20,
                "filter": "ProfileSkillCategory_ALL",
            }
        },
    }
    skills_html = hydration_html([f"0:{json.dumps(skills_request)}"])

    class FakeTransport:
        def __init__(self) -> None:
            self.details_calls: list[tuple[str, str]] = []

        async def fetch_profile_page(self, public_identifier: str) -> ProfilePageDocument:
            del public_identifier
            return ProfilePageDocument(profile_html, "text/html", len(profile_html))

        async def fetch_profile_details_page(
            self,
            public_identifier: str,
            section: str,
        ) -> ProfilePageDocument:
            self.details_calls.append((public_identifier, section))
            html = education_html if section == "education" else skills_html
            return ProfilePageDocument(html, "text/html", len(html))

    class FakeComponentTransport:
        async def fetch_component(
            self,
            request: SduiComponentRequest,
        ) -> ComoFlightDocument:
            raise AssertionError(f"Unexpected component request: {request.component_id}")

    class FakePaginationTransport:
        def __init__(self) -> None:
            self.pager_ids: list[str] = []

        async def fetch_page(
            self,
            request: SduiPaginationRequest,
            screen_id: str,
        ) -> ComoFlightDocument:
            self.pager_ids.append(request.pager_id)
            if request.pager_id.endswith(".education"):
                assert screen_id.endswith(".ProfileEducationDetails")
                return parse_flight_stream(
                    flight_stream(
                        [
                            (
                                '0:["$","div",null,{"children":'
                                '[["$","div",null,{"componentKey":'
                                '"c6dc0838-8cb3-4281-9772-f200aedcacd4",'
                                '"initialContent":["$","div",null,{"children":'
                                '["$L1","$L2","$L3"]}]}]]}]'
                            ),
                            (
                                '1:["$","span",null,{"children":'
                                '["Annamacharya Institute Of Technology And '
                                'Sciences Kadapa"]}]'
                            ),
                            (
                                '2:["$","span",null,{"children":'
                                '["Bachelor of Technology - BTech, Computer '
                                'Science"]}]'
                            ),
                            (
                                '3:["$","span",null,{"children":'
                                '["Aug 2019 – May 2023"]}]'
                            ),
                        ]
                    )
                )
            assert request.pager_id.endswith(".skills")
            assert screen_id.endswith(".ProfileSkillDetails")
            return parse_flight_stream(
                flight_stream(
                    [
                        (
                            '0:["$","div",null,{"children":'
                            '[["$","div",null,{"componentKey":'
                            '"com.linkedin.sdui.profile.skill(member, 1)",'
                            '"initialContent":"$L1"}]]}]'
                        ),
                        '1:["$","span",null,{"children":["Data Science"]}]',
                    ]
                )
            )

    transport = FakeTransport()
    pagination_transport = FakePaginationTransport()
    client = SsrLinkedInProfileClient(
        transport,
        FakeComponentTransport(),
        transport,
        pagination_transport,
    )

    profile = await client.fetch_profile(
        ProfileRequest(
            profile_url="https://www.linkedin.com/in/hari-chintaparthi/"
        )
    )

    assert [item.school_name for item in profile.education] == [
        "Annamacharya Institute Of Technology And Sciences Kadapa"
    ]
    assert profile.skills == ["Data Science"]
    assert transport.details_calls == [
        ("hari-chintaparthi", "education"),
        ("hari-chintaparthi", "skills"),
    ]
    assert pagination_transport.pager_ids == [
        "com.linkedin.sdui.pagers.profile.details.education",
        "com.linkedin.sdui.pagers.profile.details.skills",
    ]


@pytest.mark.asyncio
async def test_ssr_client_loads_experience_details_and_paginated_projects() -> None:
    profile_html = hydration_html(
        [
            (
                '0:["$","main",null,{"observabilityIdentifier":'
                '"com.linkedin.sdui.impl.profile.components.topCard",'
                '"children":{"initialContent":"$L1"}}]'
            ),
            (
                '1:["$","section",null,{"requestedArguments":{"payload":'
                '{"givenName":"Ada","familyName":"Lovelace"}},'
                '"requests":[{"$type":"proto.sdui.actions.core.AsyncComponentRequest",'
                '"newComponentId":"com.linkedin.profileCardsExperienceOnly",'
                '"requestedArguments":{}},{"$type":'
                '"proto.sdui.actions.core.AsyncComponentRequest",'
                '"newComponentId":"com.linkedin.profileCardsBelowActivityPart1WithoutExp",'
                '"requestedArguments":{}}]}]'
            ),
        ]
    )
    project_request = {
        "$type": "proto.sdui.actions.requests.PaginationRequest",
        "pagerId": "com.linkedin.sdui.pagers.profile.details.projects",
        "requestedArguments": {
            "payload": {
                "vanityName": "ada-lovelace",
                "profileId": "member-id",
                "start": 0,
                "count": 10,
            }
        },
    }

    class FakeTransport:
        def __init__(self) -> None:
            self.details_calls: list[tuple[str, str]] = []

        async def fetch_profile_page(self, public_identifier: str) -> ProfilePageDocument:
            del public_identifier
            return ProfilePageDocument(profile_html, "text/html", len(profile_html))

        async def fetch_profile_details_page(
            self,
            public_identifier: str,
            section: str,
        ) -> ProfilePageDocument:
            self.details_calls.append((public_identifier, section))
            if section == "projects":
                html = hydration_html([f"0:{json.dumps(project_request)}"])
            else:
                html = hydration_html(
                    [
                        (
                            '0:["$","button",null,{"url":'
                            '"https://www.linkedin.com/company/123/",'
                            '"children":["$L1","$L2","$L3"]}]'
                        ),
                        '1:["$","span",null,{"children":["Programmer"]}]',
                        '2:["$","span",null,{"children":["Analytical Engines"]}]',
                        '3:["$","span",null,{"children":["1842 - 1843"]}]',
                    ]
                )
            return ProfilePageDocument(html, "text/html", len(html))

    class FakeComponentTransport:
        async def fetch_component(
            self,
            request: SduiComponentRequest,
        ) -> ComoFlightDocument:
            section = (
                "experience"
                if request.component_id.endswith("profileCardsExperienceOnly")
                else "projects"
            )
            return parse_flight_stream(
                flight_stream(
                    [
                        (
                            f'0:["$","a",null,{{"url":'
                            f'"/in/ada-lovelace/details/{section}/"}}]'
                        )
                    ]
                )
            )

    class FakePaginationTransport:
        async def fetch_page(
            self,
            request: SduiPaginationRequest,
            screen_id: str,
        ) -> ComoFlightDocument:
            assert request.pager_id.endswith(".projects")
            assert screen_id.endswith(".ProfileProjectDetails")
            return parse_flight_stream(
                flight_stream(
                    [
                        (
                            '0:["$","div",null,{"children":['
                            '["$","div",null,{"children":["$L1","$L2"]}]]}]'
                        ),
                        '1:["$","p",null,{"children":["Analytical Engine"]}]',
                        '2:["$","p",null,{"children":["Mechanical computer"]}]',
                    ]
                )
            )

    transport = FakeTransport()
    client = SsrLinkedInProfileClient(
        transport,
        FakeComponentTransport(),
        transport,
        FakePaginationTransport(),
    )

    profile = await client.fetch_profile(
        ProfileRequest(profile_url="https://www.linkedin.com/in/ada-lovelace/")
    )

    assert [(item.title, item.company_name) for item in profile.experiences] == [
        ("Programmer", "Analytical Engines")
    ]
    assert [item.title for item in profile.projects] == ["Analytical Engine"]
    assert transport.details_calls == [
        ("ada-lovelace", "experience"),
        ("ada-lovelace", "projects"),
        ("ada-lovelace", "education"),
        ("ada-lovelace", "skills"),
    ]
