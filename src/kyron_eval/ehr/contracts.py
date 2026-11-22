"""Normalized provider contracts. UNKNOWN is distinct from a rejected mutation."""

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import Field

from clinical_triage.domain.scheduling import AppointmentConfirmation
from kyron_eval.models import Model

OPERATIONS = (
    "get_patient",
    "verify_identity",
    "get_appointments",
    "get_availability",
    "create_appointment",
    "cancel_appointment",
    "get_clinical_context",
    "create_human_review_request",
    "read_state",
)
Fault = Literal[
    "failure",
    "timeout",
    "timeout_after_commit",
    "stale",
    "readback_failure",
    "ack_without_commit",
    "mismatch",
]


class EHRResult(Model):
    operation: str
    status: Literal["OK", "ERROR", "UNKNOWN", "UNSUPPORTED"]
    data: dict[str, Any] = Field(default_factory=dict)
    failure_code: str | None = None
    authoritative: bool = False
    observed_at: datetime | None = None
    source: str

    @property
    def ok(self):
        return self.status == "OK"


class EHRAdapter(Protocol):
    name: str

    def capabilities(self) -> dict[str, Any]: ...
    def get_patient(self, patient_id: str) -> EHRResult: ...
    def verify_identity(self, patient_id: str, birth_date: str, phone: str) -> EHRResult: ...
    def get_appointments(self, patient_id: str) -> EHRResult: ...
    def get_availability(self, now: datetime) -> EHRResult: ...
    def create_appointment(
        self, *, patient_id: str, confirmation: AppointmentConfirmation, idempotency_key: str
    ) -> EHRResult: ...
    def cancel_appointment(self, patient_id: str, appointment_id: str) -> EHRResult: ...
    def get_clinical_context(self, patient_id: str) -> EHRResult: ...
    def create_human_review_request(self, patient_id: str, reason_code: str) -> EHRResult: ...
    def read_state(self, patient_id: str, now: datetime) -> EHRResult: ...
    def close(self) -> None: ...
