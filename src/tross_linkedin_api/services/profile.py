"""LinkedIn profile retrieval orchestration."""

from tross_linkedin_api.clients.base import LinkedInProfileClient
from tross_linkedin_api.schemas.profile import ProfileRequest, ProfileResponse


class ProfileService:
    def __init__(self, client: LinkedInProfileClient) -> None:
        self._client = client

    async def retrieve(self, request: ProfileRequest) -> ProfileResponse:
        profile = await self._client.fetch_profile(request)
        return ProfileResponse(profile=profile)

