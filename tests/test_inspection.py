import stat
from pathlib import Path

import pytest

from tross_linkedin_api.clients.linkedin import ProfilePageDocument
from tross_linkedin_api.inspection import inspect_profile_page


class FakeProfilePageTransport:
    def __init__(self, document: ProfilePageDocument) -> None:
        self.document = document
        self.identifiers: list[str] = []

    async def fetch_profile_page(self, public_identifier: str) -> ProfilePageDocument:
        self.identifiers.append(public_identifier)
        return self.document


@pytest.mark.asyncio
async def test_inspection_saves_private_html_and_returns_only_summary(
    tmp_path: Path,
) -> None:
    html = """
    <html><head><meta name="pageKey" content="sensitive-value"></head>
    <body>com.linkedin.sdui.generated.profile.dsl.impl.profileCardsActivity</body>
    </html>
    """
    document = ProfilePageDocument(
        html=html,
        content_type="text/html",
        content_length_bytes=len(html.encode()),
    )
    transport = FakeProfilePageTransport(document)
    output_path = tmp_path / "profile-page.html"

    summary = await inspect_profile_page(
        "https://www.linkedin.com/in/ada-lovelace/",
        transport,
        output_path=output_path,
    )

    assert transport.identifiers == ["ada-lovelace"]
    assert output_path.read_text() == html
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    assert "sensitive-value" not in summary.model_dump_json()


@pytest.mark.asyncio
async def test_inspection_does_not_write_raw_html_without_explicit_output(
    tmp_path: Path,
) -> None:
    document = ProfilePageDocument(
        html="<html></html>",
        content_type="text/html",
        content_length_bytes=13,
    )
    transport = FakeProfilePageTransport(document)

    await inspect_profile_page(
        "https://www.linkedin.com/in/ada-lovelace/",
        transport,
    )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_inspection_refuses_symlink_output(tmp_path: Path) -> None:
    document = ProfilePageDocument(
        html="<html>sensitive</html>",
        content_type="text/html",
        content_length_bytes=22,
    )
    transport = FakeProfilePageTransport(document)
    target = tmp_path / "target.html"
    target.write_text("keep-me")
    link = tmp_path / "profile-page.html"
    link.symlink_to(target)

    with pytest.raises(OSError):
        await inspect_profile_page(
            "https://www.linkedin.com/in/ada-lovelace/",
            transport,
            output_path=link,
        )

    assert target.read_text() == "keep-me"
