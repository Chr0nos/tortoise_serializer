from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field


class Metric(BaseModel):
    value: int
    rationale: str | None = None


class MessageMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    thread_id: int
    sender_id: int
    receiver_id: int
    metrics: dict[str, Metric] = Field(default_factory=dict)
    begin: timedelta | None = None
    end: timedelta | None = None
