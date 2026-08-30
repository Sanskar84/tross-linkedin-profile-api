"""LinkedIn client interface used by the service layer."""

from typing import Protocol

from tross_linkedin_api.schemas.profile import LinkedInProfile, ProfileRequest


class LinkedInProfileClient(Protocol):
    async def fetch_profile(self, request: ProfileRequest) -> LinkedInProfile:
        """Fetch and normalize a LinkedIn profile."""
        ...

