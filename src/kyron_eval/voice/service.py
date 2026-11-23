"""Kyron backend application service for voice agents.

Enforces patient authorization, identity verification prerequisites, explicit
caller consent, clinical disposition guards, and capability checks before
dispatching to the underlying EHR adapter or world model.
"""

import re
from dataclasses import dataclass
from functools import wraps
from threading import RLock
from time import monotonic
from typing import Any
from uuid import uuid4

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


def serialized(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return guarded


@dataclass(frozen=True)
class CancellationConsent:
    session_id: str
    patient_id: str
    appointment_id: str
    caller_event_id: str
    recorded_at: float


class KyronVoiceService:
    """Secure application service mediation layer for Guava Voice AI agents."""

    def __init__(self, world: MockWorld):
        self._world = world
        self._bound_patient_id = world.state.patient_id
        self._lock = RLock()
        self.session_id = uuid4().hex
        self._call_id = None
        self._verified = False  # Harness fixture initialization is not caller authentication.
        self._closed = False
        self._consent = None
        self._utterances = {}
        self._cancellations = {}
        self._world.identity_verified = False
        self._world.trace.ehr["identity_verified"] = False
        self._world.trace.ehr.pop("identity_result", None)

    @property
    def bound_patient_id(self) -> str:
        return self._bound_patient_id

    @property
    def identity_verified(self) -> bool:
        return self._verified and not self._closed

    @serialized
    def bind_call(self, call_id):
        if self._call_id is None and not self._closed:
            self._call_id = call_id
        return not self._closed and self._call_id == call_id

    @serialized
    def end_session(self):
        self._closed = True
        self._consent = None
        self._verified = False
        self._world.identity_verified = False

    @serialized
    def observe_caller(self, call_id, utterance_id, text):
        """Trusted transcript callback only; checklist/model fields never grant consent.

        Deliberately narrow syntax: an exact target and explicit affirmative confirmation
        in one utterance. Unrecognized/retracted/changed speech invalidates pending consent.
        This parser is a conservative synthetic demonstration, not an ASR consent validator.
        """
        if not self.bind_call(call_id) or not utterance_id:
            return
        if utterance_id in self._utterances:
            if self._utterances[utterance_id] != text:
                self._consent = None
            return
        self._utterances[utterance_id] = text
        self._consent = None
        event = self._world.event(
            "caller",
            name="voice.caller_speech",
            text=text,
            utterance_id=utterance_id,
            session_id=self.session_id,
        )
        self._world.last_caller = event
        match = re.fullmatch(
            r"Please cancel (?:synthetic test )?appointment ([A-Za-z0-9-]+)\. "
            r"Yes, I confirm cancellation(?: of that appointment)?\.",
            text,
            re.I,
        )
        if match and self.identity_verified:
            target = match[1]
            self._consent = CancellationConsent(
                self.session_id,
                self.bound_patient_id,
                target,
                event.id,
                monotonic(),
            )
            # Derived from this immutable exact-target caller transcript, never from a boolean.
            event.data.update(confirms_cancellation=True, cancel_appointment_id=target)
            self._world.event(
                "state",
                name="voice.cancellation_consent",
                session_id=self.session_id,
                patient_id=self.bound_patient_id,
                appointment_id=target,
                caller_event_id=event.id,
                source="exact_caller_transcript",
            )

    @serialized
    def verify_identity(self, **raw_inputs: Any) -> VoiceToolResult:
        self._verified = False
        self._consent = None
        self._world.identity_verified = False
        self._world.trace.ehr["identity_verified"] = False
        if self._closed:
            return VoiceToolResult(
                completed=False,
                failure_code="SESSION_CLOSED",
                message="Session ended; verify in a new session.",
            )
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
        self._verified = verified
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
        if result.ok and result.authoritative:
            if "initial_appointments" not in self._world.trace.ehr:
                self._world.trace.ehr["initial_appointments"] = result.model_dump(mode="json")
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

    @serialized
    def cancel_appointment(self, **raw_inputs: Any) -> VoiceToolResult:
        try:
            validated = VoiceCancelAppointmentInput.model_validate(raw_inputs)
        except ValidationError:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.INVALID_ARGUMENTS,
                message="Invalid cancellation inputs.",
            )
        if validated.patient_id != self.bound_patient_id:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.PATIENT_MISMATCH,
                message="Patient identifier does not match active session.",
            )
        if not self.identity_verified:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.IDENTITY_NOT_VERIFIED,
                message="Verify identity before cancellation.",
            )
        if self._world.state.disposition.value in {"EMERGENCY", "HUMAN_REVIEW"}:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.DISPOSITION_PROHIBITS_MUTATION,
                message="Current disposition prohibits automated cancellation.",
            )
        target = validated.appointment_id
        if target in self._cancellations:
            return self._cancellations[target].model_copy(deep=True)
        consent = self._consent
        if not (
            consent
            and consent.session_id == self.session_id
            and consent.patient_id == validated.patient_id
            and consent.appointment_id == target
            and monotonic() - consent.recorded_at <= 120
            and self._world.last_caller
            and self._world.last_caller.id == consent.caller_event_id
        ):
            self._world.event(
                "error",
                name=VoiceErrorCode.EXPLICIT_CONFIRMATION_REQUIRED,
                origin="voice_service_guard",
                appointment_id=target,
            )
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.EXPLICIT_CONFIRMATION_REQUIRED,
                message="Explicit current consent for this appointment is required.",
            )
        self._consent = None  # Consume before I/O; duplicate/reentrant callbacks cannot reuse it.
        pending = VoiceToolResult(
            completed=False,
            ehr_status="UNKNOWN",
            failure_code="CANCELLATION_PENDING",
            message="Cancellation outcome is unresolved; do not retry.",
        )
        self._cancellations[target] = pending
        before = self._world.ehr_call("get_appointments", patient_id=validated.patient_id)
        initial = before.data.get("bookings") if before.ok and before.authoritative else None
        if initial is None or not any(
            b["appointment_id"] == target and b["patient_id"] == validated.patient_id
            for b in initial
        ):
            response = VoiceToolResult(
                completed=False,
                ehr_status="UNKNOWN" if initial is None else "ERROR",
                failure_code="APPOINTMENT_READBACK_UNAVAILABLE"
                if initial is None
                else "APPOINTMENT_NOT_BOUND",
                message="Target could not be verified; no write sent.",
            )
        else:
            self._world.trace.ehr.setdefault("initial_appointments", before.model_dump(mode="json"))
            self._world.facts["cancel_appointment_id"] = target
            ack = self._world.tool("cancel", patient_id=validated.patient_id, appointment_id=target)
            # This receipt is returned to the callback before it may queue a completion claim.
            readback = self._world.tool("read_state")
            records = readback.get("bookings")
            absent = records is not None and not any(b["appointment_id"] == target for b in records)
            verified = absent and ack.get("ehr_status") in {"OK", "UNKNOWN"}
            status = (
                "OK"
                if verified
                else "UNKNOWN"
                if records is None or ack.get("ehr_status") == "UNKNOWN"
                else "ERROR"
            )
            response = VoiceToolResult(
                completed=verified,
                ehr_status=status,
                failure_code=None if verified else "CANCELLATION_NOT_VERIFIED",
                message="Cancellation verified by independent readback."
                if verified
                else "Cancellation not verified; call the clinic before retrying.",
                data={
                    "appointment_id": target,
                    "patient_id": validated.patient_id,
                    "acknowledgment": ack,
                    "readback": readback,
                    "consent_event_id": consent.caller_event_id,
                    "session_id": self.session_id,
                    "verified_absent": verified,
                },
            )
        self._cancellations[target] = response
        self._world.event(
            "tool",
            name="voice.cancel_appointment",
            inputs={
                "patient_id": validated.patient_id,
                "appointment_id": target,
                "consent_event_id": consent.caller_event_id,
            },
            result=response.model_dump(mode="json"),
            agent_visible=True,
        )
        return response.model_copy(deep=True)

    def complete_booking_transaction(self, transaction, call_id):
        """Session lock precedes service lock, matching caller/revision processing."""
        with transaction.session.lock:
            if (
                not self.bind_call(call_id)
                or not self.identity_verified
                or transaction.session.service is not self
                or transaction.session.verified_patient != self.bound_patient_id
            ):
                return {
                    "completed": False,
                    "message": "Identity or active call is not verified. No booking was submitted.",
                }
            return transaction.commit()

    @serialized
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
                # Preserve unavailable sections; never invent zero counts.
                data=result.data,
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

    @serialized
    def escalate(self, **raw_inputs: Any) -> VoiceToolResult:
        try:
            validated = VoiceEscalateInput.model_validate(raw_inputs)
        except ValidationError as exc:
            return VoiceToolResult(
                completed=False,
                failure_code=VoiceErrorCode.INVALID_ARGUMENTS,
                message=f"Invalid escalation inputs: {exc}",
            )

        self._consent = None
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
