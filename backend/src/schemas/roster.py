from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class RosterShiftInput(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    slots: List[str] = Field(min_length=2, max_length=5000)


class RosterLockInput(BaseModel):
    seed: Optional[str] = Field(default=None, max_length=64)
    shifts: List[RosterShiftInput] = Field(min_length=1, max_length=100)


class RosterClaimInput(BaseModel):
    worker: Optional[str] = Field(default=None, max_length=120)


class RosterReadingsInput(BaseModel):
    reading1: float
    reading2: float
    actual_order: Literal['AB', 'BA']
