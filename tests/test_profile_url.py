import pytest
from pydantic import ValidationError

from tross_linkedin_api.schemas.profile import ProfileRequest


@pytest.mark.parametrize(
    ("url", "expected_identifier"),
    [
        ("https://www.linkedin.com/in/ada-lovelace/", "ada-lovelace"),
        ("https://linkedin.com/in/ada-lovelace?trk=public_profile", "ada-lovelace"),
        ("http://www.linkedin.com/in/Grace-Hopper", "Grace-Hopper"),
    ],
)
def test_profile_url_extracts_public_identifier(url: str, expected_identifier: str) -> None:
    request = ProfileRequest(profile_url=url)

    assert request.public_identifier == expected_identifier
    assert request.canonical_url == f"https://www.linkedin.com/in/{expected_identifier}/"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/in/ada-lovelace",
        "https://www.linkedin.com/company/openai",
        "https://www.linkedin.com/in/",
        "not-a-url",
    ],
)
def test_profile_url_rejects_non_profile_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        ProfileRequest(profile_url=url)

