"""Synthetic evaluation evidence. Free text must never enter operational audit records."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from clinical_triage.domain.disposition import Disposition
from clinical_triage.domain.scheduling import AppointmentSlot


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Status(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NA = "N/A"
    ERROR = "ERROR"


class CallerTurn(Model):
    text: str = Field(min_length=1)
    facts: dict[str, str] = Field(default_factory=dict)
    confirms_offer: bool = False
    confirms_cancellation: bool = False
    elapsed_seconds: int = Field(default=0, ge=0)


class Booking(Model):
    appointment_id: str
    patient_id: str
    slot: AppointmentSlot
    confirmation_event_id: str


class Review(Model):
    record_id: str
    patient_id: str
    reason_code: str


class SystemState(Model):
    patient_id: str = Field(pattern=r"^synthetic-")
    slots: list[AppointmentSlot]
    bookings: list[Booking] = Field(default_factory=list)
    reviews: list[Review] = Field(default_factory=list)
    disposition: Disposition = Disposition.ROUTINE


class ToolBehavior(Model):
    booking: Literal["commit", "failure", "ack_without_commit"] = "commit"
    review: Literal["commit", "failure", "ack_without_commit"] = "commit"
    snapshot_ttl_seconds: int = Field(default=300, ge=1)


class Expectations(Model):
    acceptable_outcomes: list[Literal["BOOKED", "NOT_BOOKED", "EMERGENCY", "REVIEW_QUEUED"]]
    disposition: Disposition
    critical_entities: dict[str, str]
    explanation: str


class Scenario(Model):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    version: str
    synthetic: Literal[True] = True
    title: str
    workflow: Literal["scheduling", "triage"]
    caller_goal: str
    script: list[CallerTurn] = Field(min_length=1)
    clock: datetime
    initial_state: SystemState
    tools: ToolBehavior = Field(default_factory=ToolBehavior)
    policy_id: str
    policy_version: str
    expected: Expectations
    transcript_cannot_prove: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def clock_is_aware(self):
        if self.clock.tzinfo is None:
            raise ValueError("scenario clock must be timezone-aware")
        return self


class Event(Model):
    id: str
    seq: int = Field(ge=1)
    at: datetime
    kind: Literal["caller", "agent", "tool", "policy", "state", "error", "disposition", "model"]
    text: str | None = None
    name: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class Trace(Model):
    schema_version: str = "1.0"
    harness_version: str = "replay-1.0"
    evidence_class: Literal[
        "SYNTHETIC_EVALUATION_TRACE", "VOICE_LIVE_SYNTHETIC"
    ] = "SYNTHETIC_EVALUATION_TRACE"
    scenario_id: str
    scenario_version: str
    scenario_sha256: str
    variant: Literal["A", "B", "LLM", "VOICE"]
    agent_version: str
    policy_id: str
    policy_version: str
    policy_sha256: str
    state_before: SystemState
    state_after: SystemState | None = None
    events: list[Event] = Field(default_factory=list)
    final_disposition: Disposition | None = None
    ehr: dict[str, Any] = Field(default_factory=dict)


class Metric(Model):
    name: str
    status: Status
    reason: str
    evidence_refs: list[str]
    evaluator_version: str
    details: dict[str, Any] = Field(default_factory=dict)


class Result(Model):
    scenario: Scenario
    trace: Trace
    metrics: list[Metric]


class Run(Model):
    schema_version: str = "1.0"
    synthetic: Literal[True] = True
    id: str
    created_at: datetime
    dataset_sha256: str
    implementation_sha256: str
    runtime: dict[str, str] = Field(default_factory=dict)
    policy_manifest: dict[str, Any] = Field(default_factory=dict)
    evaluator_version: str
    variants: list[Literal["A", "B", "LLM", "VOICE"]]
    configuration: dict[str, Any] = Field(default_factory=dict)
    results: list[Result]


class HumanLabel(Model):
    scenario_id: str
    variant: Literal["A", "B", "LLM", "VOICE"]
    metric: Literal["resolution_clarity"] = "resolution_clarity"
    label: Literal["PASS", "FAIL", "UNKNOWN"]
    rationale: str = Field(min_length=5, max_length=4000)
    reviewer: str = Field(min_length=1, max_length=100)
    source: Literal["human"] = "human"
    rubric_version: str = "clarity-rubric-1"
    trace_sha256: str
    created_at: datetime
