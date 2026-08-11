from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TargetReference(FrozenModel):
    kind: str = Field(min_length=1)
    value: str = Field(min_length=1)
