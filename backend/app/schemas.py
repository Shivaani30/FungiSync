from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.models import BatchStatus, OrderStatus

class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class GrowerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=1, max_length=30)
    location: str = Field(min_length=1, max_length=200)
    @field_validator("name", "phone", "location")
    @classmethod
    def clean(cls, value: str) -> str: return value.strip()
class GrowerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, min_length=1, max_length=30)
    location: str | None = Field(default=None, min_length=1, max_length=200)
class GrowerOut(ORM):
    id: UUID; name: str; phone: str; location: str; created_at: datetime; updated_at: datetime

class BuyerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=200)
    contact: str = Field(min_length=1, max_length=200)
class BuyerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, min_length=1, max_length=200)
    contact: str | None = Field(default=None, min_length=1, max_length=200)
class BuyerOut(ORM):
    id: UUID; name: str; location: str; contact: str; created_at: datetime; updated_at: datetime

class BatchCreate(BaseModel):
    grower_id: UUID; species: str = Field(min_length=1, max_length=100)
    expected_kg: Decimal = Field(ge=0, max_digits=12, decimal_places=3)
    confirmed_kg: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    harvest_date: date; asking_price_per_kg: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    quality_grade: str | None = Field(default=None, min_length=1, max_length=30)
    best_before_date: date | None = None; status: BatchStatus = BatchStatus.EXPECTED
    @field_validator("quality_grade")
    @classmethod
    def clean_quality_grade(cls, value: str | None) -> str | None:
        if value is None: return None
        value = value.strip()
        if not value: raise ValueError("quality_grade must not be blank")
        return value
    @model_validator(mode="after")
    def validate_best_before_date(self):
        if self.best_before_date and self.best_before_date < self.harvest_date:
            raise ValueError("best_before_date must be on or after harvest_date")
        return self
class BatchUpdate(BaseModel):
    grower_id: UUID | None = None; species: str | None = Field(default=None, min_length=1, max_length=100)
    expected_kg: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    confirmed_kg: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    harvest_date: date | None = None; asking_price_per_kg: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    quality_grade: str | None = Field(default=None, min_length=1, max_length=30)
    best_before_date: date | None = None; status: BatchStatus | None = None
    @field_validator("quality_grade")
    @classmethod
    def clean_quality_grade(cls, value: str | None) -> str | None:
        if value is None: return None
        value = value.strip()
        if not value: raise ValueError("quality_grade must not be blank")
        return value
class BatchOut(ORM):
    id: UUID; grower_id: UUID; species: str; expected_kg: Decimal; confirmed_kg: Decimal | None; harvest_date: date; asking_price_per_kg: Decimal | None; quality_grade: str | None; best_before_date: date | None; status: BatchStatus; created_at: datetime; updated_at: datetime

class OrderCreate(BaseModel):
    buyer_id: UUID; species: str = Field(min_length=1, max_length=100)
    quantity_kg: Decimal = Field(ge=0, max_digits=12, decimal_places=3)
    price_limit: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    delivery_date: date; required_quality_grade: str | None = Field(default=None, min_length=1, max_length=30); status: OrderStatus = OrderStatus.PENDING
    @field_validator("required_quality_grade")
    @classmethod
    def clean_required_quality_grade(cls, value: str | None) -> str | None:
        if value is None: return None
        value = value.strip()
        if not value: raise ValueError("required_quality_grade must not be blank")
        return value
class OrderUpdate(BaseModel):
    buyer_id: UUID | None = None; species: str | None = Field(default=None, min_length=1, max_length=100)
    quantity_kg: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    price_limit: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    delivery_date: date | None = None; required_quality_grade: str | None = Field(default=None, min_length=1, max_length=30); status: OrderStatus | None = None
    @field_validator("required_quality_grade")
    @classmethod
    def clean_required_quality_grade(cls, value: str | None) -> str | None:
        if value is None: return None
        value = value.strip()
        if not value: raise ValueError("required_quality_grade must not be blank")
        return value
class OrderOut(ORM):
    id: UUID; buyer_id: UUID; species: str; quantity_kg: Decimal; price_limit: Decimal; delivery_date: date; required_quality_grade: str | None; status: OrderStatus; created_at: datetime; updated_at: datetime

class MatchCriterionOut(BaseModel):
    status: str
    weight: int
    points: Decimal
    reason: str

class MatchCandidateOut(BaseModel):
    batch_id: UUID
    grower_id: UUID
    order_id: UUID | None = None
    buyer_id: UUID | None = None
    eligible: bool
    compatibility_score: Decimal
    data_completeness_percentage: Decimal
    requested_quantity_kg: Decimal
    available_quantity_kg: Decimal | None
    quantity_that_could_be_supplied_kg: Decimal
    remaining_quantity_kg: Decimal
    criteria: dict[str, MatchCriterionOut]
    positive_reasons: list[str]
    unknown_criteria: list[str]
    warnings: list[str]
    exclusion_reasons: list[str]
    rank: int | None

class MatchResultsOut(BaseModel):
    target_id: UUID
    total_candidates: int
    candidates: list[MatchCandidateOut]
