import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.api.pagination import Page
from app.api.schemas.base import BaseSchema

# Caps (D21) are enforced by the services; the schema bounds what is parsed at all.
RiskIn = Literal["low", "medium", "high"]
# Must match decision_reads.SORTS; a test walks every value.
Sort = Literal["created_at", "-created_at", "updated_at", "-updated_at", "title", "-title"]


class DecisionIn(BaseSchema):
    title: str = Field(max_length=200)
    description: str = Field(default="", max_length=4096)
    action_type: str = Field(max_length=64)
    payload: dict[str, Any]
    risk_level: RiskIn


class ReviseIn(BaseSchema):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4096)
    action_type: str | None = Field(default=None, max_length=64)
    payload: dict[str, Any] | None = None
    risk_level: RiskIn | None = None


class DecisionOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    action_type: str
    payload: dict[str, Any]
    risk_level: str
    effective_risk_level: str
    status: str
    version: int
    content_hash: str
    requested_by_agent_id: uuid.UUID | None
    requested_by_user_id: uuid.UUID | None
    voting_deadline_at: datetime
    escalation_reason: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class VoteIn(BaseSchema):
    # strict: a JSON `true` must not become version 1 or confidence 1.0
    request_version: int = Field(strict=True)
    verdict: Literal["approve", "reject", "abstain"]
    confidence: float = Field(strict=True)
    rationale: str = Field(default="", max_length=4096)


class VoteCast(BaseModel):
    vote_id: uuid.UUID
    round: int
    cast: int
    quorum: int
    status: str


class VoteOut(BaseModel):
    session_id: uuid.UUID
    round: int
    verdict: str
    confidence: float
    rationale: str
    request_version: int
    created_at: datetime
    revealed_at: datetime | None


class VotesPage(Page[VoteOut]):
    # "2 of 3 cast" is visible even while round 1 hides the votes themselves.
    cast: int
    quorum: int


class MessageIn(BaseSchema):
    body: str = Field(max_length=4096)


class MessagePosted(BaseModel):
    id: uuid.UUID
    request_version: int
    author_type: str


class MessageOut(BaseModel):
    id: uuid.UUID
    author_type: str
    author_id: uuid.UUID | None
    body: str
    request_version: int
    created_at: datetime
