"""Validated API inputs, structured agent advice and explicit response shapes."""

import re
from datetime import date
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

PositiveID = Annotated[int, Field(gt=0, le=9_007_199_254_740_991, strict=True)]
Name = Annotated[str, Field(min_length=1, max_length=160)]
Posture = Literal["NORMAL", "DELAYED_OPENING", "EARLY_DISMISSAL", "TELEWORK", "CLOSED"]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LocationFields(InputModel):
    name: Name
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    timezone: str
    open_time: str
    close_time: str

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        """Require an installed IANA zone for later date-aware shift matching."""
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Use a valid IANA timezone, e.g. America/Chicago") from error
        return value

    @field_validator("open_time", "close_time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        """Accept same-day HH:MM hours, with no implicit overnight conversion."""
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Use local 24-hour HH:MM")
        return value

    @model_validator(mode="after")
    def same_day_hours(self) -> "LocationFields":
        """The MVP explicitly supports same-day facility operating hours."""
        if self.close_time <= self.open_time:
            raise ValueError("close_time must be after open_time on the same day")
        return self


class LocationCreate(LocationFields):
    id: PositiveID | None = None


class LocationUpdate(InputModel):
    id: PositiveID | None = None
    name: Name | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    timezone: str | None = None
    open_time: str | None = None
    close_time: str | None = None

    @model_validator(mode="after")
    def nonempty_update(self) -> "LocationUpdate":
        """Omitted fields are preserved; explicitly clearing required fields is invalid."""
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("Provide at least one non-null facility field")
        return self


class Location(LocationFields):
    id: int
    status: Posture
    status_reason: str | None
    status_confidence: float | None
    affected_start: str | None
    affected_end: str | None
    last_checked_at: str | None
    updated_at: str


class LoginRequest(InputModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    emp_id: PositiveID
    password: str | None = None


class Manager(BaseModel):
    emp_id: int
    name: str
    email: str
    location_id: int


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    manager: Manager


class Employee(BaseModel):
    emp_id: int
    name: str
    email: str
    location_id: int
    is_manager: bool
    is_essential: bool
    is_teleworking: bool
    shift_id: int | None
    shift_name: str | None
    weekly_hours: dict[str, list[str] | None] | None


class DecisionSummary(BaseModel):
    id: int
    location_id: int
    evaluated_at: str
    posture: Posture
    reason: str
    confidence: float
    affected_start: str | None
    affected_end: str | None
    manager_status: Literal["PENDING", "APPROVED", "REJECTED"]


class EmployeeRecipient(Employee):
    included: bool
    reason: str
    requires_manager_review: bool
    warning: str | None


class Decision(DecisionSummary):
    evidence: list[dict[str, Any]]
    thresholds: list[dict[str, Any]]
    alternatives: dict[str, Any]
    recipients: list[dict[str, Any]]
    draft_subject: str
    draft_body: str
    reviewed_by: int | None
    reviewed_at: str | None
    sent_at: str | None
    delivery: dict[str, Any] | None
    manager_alert_sent_at: str | None


class Health(BaseModel):
    status: Literal["ok"]


class WeatherAlertSnapshot(BaseModel):
    available: bool
    fetched_at: str
    source_url: str
    alerts: list[dict[str, Any]]
    error: str | None = None


class Evidence(InputModel):
    source: str = Field(min_length=1)
    url: str = Field(min_length=1)
    observed_at: AwareDatetime
    value: Any


class AgentAdvice(InputModel):
    """Only the fields the adviser must generate; source facts stay in the backend."""
    posture: Posture
    reason: str = Field(min_length=20, max_length=3000)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    what_would_change: list[str] = Field(min_length=1, max_length=8)
    uncertainty: str = Field(min_length=1, max_length=2000)


class Recommendation(AgentAdvice):
    affected_start: AwareDatetime
    affected_end: AwareDatetime
    evidence: list[Evidence] = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_window(self) -> "Recommendation":
        """Reject empty or backwards intervals before storing recommendations."""
        if self.affected_end <= self.affected_start:
            raise ValueError("Impact end must follow impact start")
        return self


class DailyAdvice(AgentAdvice):
    date: date
    reason: str = Field(min_length=20, max_length=700)
    uncertainty: str = Field(min_length=1, max_length=300)
    what_would_change: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(min_length=1, max_length=3)


class WeeklyAdvice(InputModel):
    days: list[DailyAdvice] = Field(min_length=1, max_length=7)


class WeeklyDay(BaseModel):
    date: str
    state: Literal["COMPLETED", "INSUFFICIENT_DATA"]
    posture: Posture | None = None
    reason: str
    warnings: list[str] = Field(default_factory=list)
    decision_ids: list[int] = Field(default_factory=list)
    message_groups: list[dict[str, Any]] = Field(default_factory=list)


class WeeklyEvaluationResult(BaseModel):
    state: Literal["COMPLETED", "INSUFFICIENT_DATA"]
    days: list[WeeklyDay] = Field(default_factory=list)
    message: str
    evaluated_at: str | None = None


class EvaluationResult(BaseModel):
    state: Literal["COMPLETED", "INSUFFICIENT_DATA"]
    decision_id: int | None = None
    message: str


class RecipientSelection(InputModel):
    scope: Literal["affected", "all_staff"] = "affected"
    posture: Posture | None = None
    include_ids: list[PositiveID] = Field(default_factory=list, max_length=500)
    exclude_ids: list[PositiveID] = Field(default_factory=list, max_length=500)


class ReviewRequest(RecipientSelection):
    status: Literal["PENDING", "APPROVED", "REJECTED"]
    posture: Posture
    reason: str = Field(min_length=20, max_length=3000)
    draft_subject: str = Field(min_length=1, max_length=240)
    draft_body: str = Field(min_length=1, max_length=20000)
    essential_confirmed: bool = False
