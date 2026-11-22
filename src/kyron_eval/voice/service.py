"""Kyron backend application service for voice agents.

Enforces patient authorization, identity verification prerequisites, explicit
caller consent, clinical disposition guards, and capability checks before
dispatching to the underlying EHR adapter or world model.
"""

from typing import Any

from pydantic import ValidationError

from clinical_triage.domain.disposition import Disposition
from clinical_triage.scheduling import SchedulingPolicyError
from kyron_eval.replay import MockWorld
from kyron_eval.voice.contracts import (
    VoiceCancelAppointmentInput,
    VoiceClinicalContextInput,
    VoiceCreateAppointmentInput,
    VoiceErrorCode,
    VoiceEscalateInput,
    VoiceGetAppointmentsInput,
    VoiceHumanReviewInput,
    VoiceToolResult,
    VoiceVerifyIdentityInput,
)


class KyronVoiceService:
    """Secure application service mediation layer for Guava Voice AI agents."""

    def __init__(self, world: MockWorld):
        self._world = world
        self._bound_patient_id = world.state.patient_id

    @property
    def bound_patient_id(self) -> str:
        return self._bound_patient_id

    @property
    def identity_verified(self) -> bool:
        return getattr(self._world, "identity_verified", False)

    def verify_identity(self, **raw_inputs: Any) -> VoiceToolResult:
        try:
            validated = VoiceVerifyIdentityInput.model_validate(raw_inputs)
        except ValidationError as exc:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.INVALID_ARGUMENTS,
                message=f"Invalid identity verification inputs: {exc}",
            )

        if validated.patient_id != self._bound_patient_id:
            self._world.event(
                "error",
                name=VoiceErrorCode.PATIENT_MISMATCH,
                origin="voice_service_guard",
                requested=validated.patient_id,
                bound=self._bound_patient_id,
            )
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.PATIENT_MISMATCH,
                message="Patient identifier does not match active session.",
            )

        result = self._world.ehr_call(
            "verify_identity",
            patient_id=validated.patient_id,
            birth_date=validated.birth_date,
            phone=validated.phone,
        )
        verified = result.ok and result.data.get("verified") is True
        self._world.identity_verified = verified
        self._world.trace.ehr["identity_verified"] = verified
        self._world.trace.ehr["identity_result"] = result.model_dump(mode="json")

        if verified:
            return VoiceToolResult(
                completed=True,
                ehr_status=result.status,
                message="Patient identity successfully verified.",
                data={"verified": True, "patient_id": validated.patient_id},
            )
        return VoiceToolResult(
            completed=False,
            ehr_status=result.status,
            failure_code=VoiceErrorCode.IDENTITY_NOT_VERIFIED,
            message="Caller identity details did not match synthetic patient record.",
            data={"verified": False},
        )

    def get_appointments(self, **raw_inputs: Any) -> VoiceToolResult:
        try:
            validated = VoiceGetAppointmentsInput.model_validate(raw_inputs)
        except ValidationError as exc:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.INVALID_ARGUMENTS,
                message=f"Invalid appointment lookup inputs: {exc}",
            )

        if validated.patient_id != self._bound_patient_id:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.PATIENT_MISMATCH,
                message="Patient identifier does not match active session.",
            )

        if not self.identity_verified:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.IDENTITY_NOT_VERIFIED,
                message="Patient identity must be verified before retrieving appointment records.",
            )

        result = self._world.ehr_call("get_appointments", patient_id=validated.patient_id)
        if result.ok:
            bookings = result.data.get("bookings", [])
            sanitized_bookings = []
            for b in bookings:
                slot_info = b.get("slot") if isinstance(b.get("slot"), dict) else {}
                sanitized_bookings.append(
                    {
                        "appointment_id": b.get("appointment_id"),
                        "patient_id": b.get("patient_id"),
                        "starts_at": slot_info.get("starts_at"),
                        "ends_at": slot_info.get("ends_at"),
                    }
                )
            return VoiceToolResult(
                completed=True,
                ehr_status=result.status,
                message=f"Found {len(sanitized_bookings)} appointment(s).",
                data={"appointments": sanitized_bookings},
            )
        return VoiceToolResult(
            completed=False,
            ehr_status=result.status,
            failure_code=result.failure_code or VoiceErrorCode.EHR_MUTATION_FAILED,
            message="Failed to retrieve appointment records from EHR.",
        )

    def cancel_appointment(self, **raw_inputs: Any) -> VoiceToolResult:
        try:
            validated = VoiceCancelAppointmentInput.model_validate(raw_inputs)
        except ValidationError as exc:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.INVALID_ARGUMENTS,
                message=f"Invalid cancellation inputs: {exc}",
            )

        if validated.patient_id != self._bound_patient_id:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.PATIENT_MISMATCH,
                message="Patient identifier does not match active session.",
            )

        if not self.identity_verified:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.IDENTITY_NOT_VERIFIED,
                message="Patient identity must be verified before cancelling an appointment.",
            )

        if not validated.confirmed_by_caller:
            self._world.event(
                "error",
                name=VoiceErrorCode.EXPLICIT_CONFIRMATION_REQUIRED,
                origin="voice_service_guard",
                appointment_id=validated.appointment_id,
            )
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.EXPLICIT_CONFIRMATION_REQUIRED,
                message="Explicit caller confirmation is required before cancelling.",
            )

        if self._world.state.disposition.value in {"EMERGENCY", "HUMAN_REVIEW"}:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.DISPOSITION_PROHIBITS_MUTATION,
                message=(
                    "Current clinical disposition prohibits automated scheduling or cancellation."
                ),
            )

        # Set caller facts & confirmation in world model so EHR contracts match
        self._world.facts["cancel_appointment_id"] = validated.appointment_id
        if self._world.last_caller:
            self._world.last_caller.data["confirms_cancellation"] = True

        result = self._world.ehr_call(
            "cancel_appointment",
            patient_id=validated.patient_id,
            appointment_id=validated.appointment_id,
        )

        completed = None if result.status == "UNKNOWN" else result.ok
        cancel_msg = (
            "Appointment cancelled successfully." if result.ok else "Cancellation failed."
        )
        return VoiceToolResult(
            completed=bool(completed),
            ehr_status=result.status,
            failure_code=result.failure_code,
            message=cancel_msg,
            data={"appointment_id": validated.appointment_id, "deleted": result.ok},
        )

    def create_appointment(self, **raw_inputs: Any) -> VoiceToolResult:
        try:
            validated = VoiceCreateAppointmentInput.model_validate(raw_inputs)
        except ValidationError as exc:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.INVALID_ARGUMENTS,
                message=f"Invalid creation inputs: {exc}",
            )

        if validated.patient_id != self._bound_patient_id:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.PATIENT_MISMATCH,
                message="Patient identifier does not match active session.",
            )

        if not self.identity_verified:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.IDENTITY_NOT_VERIFIED,
                message="Patient identity must be verified before booking.",
            )

        if not validated.confirmed_by_caller:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.EXPLICIT_CONFIRMATION_REQUIRED,
                message="Explicit caller confirmation is required before booking.",
            )

        if self._world.state.disposition.value in {"EMERGENCY", "HUMAN_REVIEW"}:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.DISPOSITION_PROHIBITS_MUTATION,
                message="Current clinical disposition prohibits automated scheduling.",
            )

        try:
            res = self._world.tool(
                "book", patient_id=validated.patient_id, slot_id=validated.slot_id
            )
            create_msg = (
                "Appointment created successfully." if res.get("completed") else "Booking rejected."
            )
            return VoiceToolResult(
                completed=bool(res.get("completed")),
                ehr_status=res.get("ehr_status", "OK"),
                failure_code=res.get("failure_code"),
                message=create_msg,
                data=res,
            )
        except (ValidationError, SchedulingPolicyError) as exc:
            return VoiceToolResult(
                completed=False,
                failure_code=getattr(exc, "reason_code", VoiceErrorCode.INVALID_ARGUMENTS),
                message=str(exc),
            )

    def get_clinical_context(self, **raw_inputs: Any) -> VoiceToolResult:
        try:
            validated = VoiceClinicalContextInput.model_validate(raw_inputs)
        except ValidationError as exc:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.INVALID_ARGUMENTS,
                message=f"Invalid context lookup inputs: {exc}",
            )

        if validated.patient_id != self._bound_patient_id:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.PATIENT_MISMATCH,
                message="Patient identifier does not match active session.",
            )

        if not self.identity_verified:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.IDENTITY_NOT_VERIFIED,
                message="Patient identity must be verified before reading clinical records.",
            )

        result = self._world.ehr_call("get_clinical_context", patient_id=validated.patient_id)
        if result.ok:
            return VoiceToolResult(
                completed=True,
                ehr_status=result.status,
                message="Clinical context retrieved.",
                data={
                    "conditions": len(result.data.get("conditions", [])),
                    "medications": len(result.data.get("medications", [])),
                    "allergies": len(result.data.get("allergies", [])),
                },
            )
        return VoiceToolResult(
            completed=False,
            ehr_status=result.status,
            failure_code=result.failure_code or VoiceErrorCode.EHR_MUTATION_FAILED,
            message="Failed to retrieve clinical context.",
        )

    def request_human_review(self, **raw_inputs: Any) -> VoiceToolResult:
        try:
            validated = VoiceHumanReviewInput.model_validate(raw_inputs)
        except ValidationError as exc:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.INVALID_ARGUMENTS,
                message=f"Invalid review request inputs: {exc}",
            )

        if validated.patient_id != self._bound_patient_id:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.PATIENT_MISMATCH,
                message="Patient identifier does not match active session.",
            )

        if not self.identity_verified:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.IDENTITY_NOT_VERIFIED,
                message="Patient identity must be verified before creating a review record.",
            )

        result = self._world.ehr_call(
            "create_human_review_request",
            patient_id=validated.patient_id,
            reason_code=validated.reason_code,
        )
        return VoiceToolResult(
            completed=result.ok,
            ehr_status=result.status,
            failure_code=result.failure_code,
            message="Human review queued." if result.ok else "Review queue rejected.",
            data=result.data,
        )

    def escalate(self, **raw_inputs: Any) -> VoiceToolResult:
        try:
            validated = VoiceEscalateInput.model_validate(raw_inputs)
        except ValidationError as exc:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.INVALID_ARGUMENTS,
                message=f"Invalid escalation inputs: {exc}",
            )

        self._world.state.disposition = Disposition.EMERGENCY
        self._world.event(
            "policy",
            name="voice.escalation",
            reason=validated.reason,
            requested_by=validated.requested_by,
            disposition=Disposition.EMERGENCY.value,
        )
        return VoiceToolResult(
            completed=True,
            ehr_status="OK",
            message="Escalation initiated. Automated scheduling locked.",
            data={"escalated": True, "disposition": "EMERGENCY"},
        )
