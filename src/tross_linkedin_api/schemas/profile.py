"""LinkedIn profile request validation and normalized response models."""

import re
from typing import Literal
from urllib.parse import unquote

from pydantic import BaseModel, Field, HttpUrl, field_validator

PUBLIC_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._~-]+$")


class ProfileRequest(BaseModel):
    profile_url: HttpUrl

    @field_validator("profile_url")
    @classmethod
    def validate_linkedin_profile_url(cls, value: HttpUrl) -> HttpUrl:
        host = (value.host or "").lower().rstrip(".")
        if host != "linkedin.com" and not host.endswith(".linkedin.com"):
            raise ValueError("profile_url must use a linkedin.com host")

        path_parts = [unquote(part) for part in (value.path or "").split("/") if part]
        if len(path_parts) < 2 or path_parts[0].lower() != "in":
            raise ValueError("profile_url must point to a LinkedIn /in/ profile")
        if not PUBLIC_IDENTIFIER_PATTERN.fullmatch(path_parts[1]):
            raise ValueError("profile_url contains an invalid public identifier")
        return value

    @property
    def public_identifier(self) -> str:
        """Extract the vanity identifier without retaining tracking parameters."""

        path = self.profile_url.path or ""
        return unquote([part for part in path.split("/") if part][1])

    @property
    def canonical_url(self) -> str:
        return f"https://www.linkedin.com/in/{self.public_identifier}/"


class DateParts(BaseModel):
    year: int | None = None
    month: int | None = Field(default=None, ge=1, le=12)


class Position(BaseModel):
    title: str | None = None
    company_name: str | None = None
    company_url: str | None = None
    location: str | None = None
    description: str | None = None
    start_date: DateParts | None = None
    end_date: DateParts | None = None


class Education(BaseModel):
    school_name: str | None = None
    degree_name: str | None = None
    field_of_study: str | None = None
    start_date: DateParts | None = None
    end_date: DateParts | None = None


class Certification(BaseModel):
    name: str | None = None
    authority: str | None = None
    license_number: str | None = None
    url: str | None = None
    start_date: DateParts | None = None
    end_date: DateParts | None = None


class Course(BaseModel):
    name: str
    number: str | None = None
    associated_with: str | None = None


class Honor(BaseModel):
    title: str
    issuer: str | None = None
    issued_on: str | None = None
    associated_with: str | None = None
    description: str | None = None
    url: str | None = None


class Language(BaseModel):
    name: str
    proficiency: str | None = None


class ProfileImage(BaseModel):
    url: str
    width: int | None = None
    height: int | None = None


class Project(BaseModel):
    title: str
    description: str | None = None
    url: str | None = None
    skills: list[str] = Field(default_factory=list)
    start_date: DateParts | None = None
    end_date: DateParts | None = None


class TestScore(BaseModel):
    name: str
    score: str | None = None
    date: str | None = None
    description: str | None = None


class Publication(BaseModel):
    title: str
    publisher: str | None = None
    published_on: str | None = None
    description: str | None = None
    url: str | None = None


class Recommendation(BaseModel):
    type: Literal["received", "given"]
    person_name: str
    person_profile_url: str | None = None
    headline: str | None = None
    date: str | None = None
    relationship: str | None = None
    text: str | None = None


class LinkedInProfile(BaseModel):
    public_identifier: str
    profile_url: str
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    headline: str | None = None
    location: str | None = None
    about: str | None = None
    experiences: list[Position] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    test_scores: list[TestScore] = Field(default_factory=list)
    publications: list[Publication] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    courses: list[Course] = Field(default_factory=list)
    honors: list[Honor] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    has_profile_photo_frame: bool = False
    profile_images: list[ProfileImage] = Field(default_factory=list)


class ProfileResponse(BaseModel):
    profile: LinkedInProfile
