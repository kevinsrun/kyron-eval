"""Validated Pydantic contracts and error codes for Guava Voice AI tools.

The voice agent communicates only through validated tool parameters and safe
sanitized payloads. Raw EHR credentials, auth tokens, and unrestricted endpoints
are never exposed to the agent.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class VoiceToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VoiceErrorCode:
    IDENTITY_NOT_VERIFIED = "IDENTITY_NOT_VERIFIED"
    PATIENT_MISMATCH = "PATIENT_MISMATCH"
    EXPLICIT_CONFIRMATION_REQUIRED = "EXPLICIT_CONFIRMATION_REQUIRED"
    DISPOSITION_PROHIBITS_MUTATION = "DISPOSITION_PROHIBITS_MUTATION"
    CAPABILITY_UNSUPPORTED = "CAPABILITY_UNSUPPORTED"
    EHR_MUTATION_FAILED = "EHR_MUTATION_FAILED"
    READBACK_UNAVAILABLE = "READBACK_UNAVAILABLE"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    NO_MATCHING_OFFER = "NO_MATCHING_OFFER"
    TRANSFER_UNAVAILABLE = "TRANSFER_UNAVAILABLE"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"


class VoiceVerifyIdentityInput(VoiceToolModel):
    patient_id: str = Field(min_length=1)
    birth_date: str = Field(min_length=4)
    phone: str = Field(min_length=4)


class VoiceGetAppointmentsInput(VoiceToolModel):
    patient_id: str = Field(min_length=1)


class VoiceCancelAppointmentInput(VoiceToolModel):
    patient_id: str = Field(min_length=1)
    appointment_id: str = Field(min_length=1)
    # Backward-compatible input only; this model-supplied flag never grants cancellation consent.
    confirmed_by_caller: bool = Field(default=False)


class VoiceCreateAppointmentInput(VoiceToolModel):
    patient_id: str = Field(min_length=1)
    slot_id: str = Field(min_length=1)
    confirmed_by_caller: bool = Field(default=False)


class VoiceClinicalContextInput(VoiceToolModel):
    patient_id: str = Field(min_length=1)


class VoiceHumanReviewInput(VoiceToolModel):
    patient_id: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)


class VoiceEscalateInput(VoiceToolModel):
    patient_id: str | None = None
    reason: str = Field(default="Caller requested escalation or safety threshold reached")
    requested_by: Literal["agent", "human"] = "agent"


class VoiceToolResult(VoiceToolModel):
    completed: bool
    ehr_status: str = "OK"
    failure_code: str | None = None
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
