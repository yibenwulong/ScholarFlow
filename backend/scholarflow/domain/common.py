from datetime import datetime, timezone
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Entity(Model):
    id: str = Field(default_factory=lambda: str(uuid4()))
    revision: int = Field(default=1, ge=1)
    created_at: AwareDatetime = Field(default_factory=utcnow)
