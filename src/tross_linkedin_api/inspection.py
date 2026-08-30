"""Safe local inspection helpers for server-rendered LinkedIn profile pages."""

import os
from pathlib import Path
from typing import Protocol

from tross_linkedin_api.clients.linkedin import ProfilePageDocument, ProfilePageSummary
from tross_linkedin_api.schemas.profile import ProfileRequest


class ProfilePageFetcher(Protocol):
    async def fetch_profile_page(self, public_identifier: str) -> ProfilePageDocument:
        """Retrieve a server-rendered profile page."""
        ...


async def inspect_profile_page(
    profile_url: str,
    transport: ProfilePageFetcher,
    *,
    output_path: Path | None = None,
) -> ProfilePageSummary:
    """Fetch a page, optionally save it privately, and return a structural summary."""

    request = ProfileRequest.model_validate({"profile_url": profile_url})
    document = await transport.fetch_profile_page(request.public_identifier)
    if output_path is not None:
        _write_private_html(output_path, document.html)
    return document.summarize(request.public_identifier)


def _write_private_html(path: Path, html: str) -> None:
    """Write sensitive diagnostic HTML with owner-only permissions."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError("Refusing to write profile HTML through a symbolic link")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    file_descriptor = os.open(path, flags, 0o600)
    with os.fdopen(file_descriptor, "w", encoding="utf-8") as output:
        output.write(html)
    os.chmod(path, 0o600)
