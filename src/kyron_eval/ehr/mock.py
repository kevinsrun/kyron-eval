"""Deterministic synthetic EHR; its state is private to one scenario/variant execution."""

from datetime import timedelta
from typing import get_args

from clinical_triage.domain.ehr import IdentityVerification, RecordResult
from clinical_triage.domain.scheduling import AppointmentMutationResult, AvailabilitySnapshot
from kyron_eval.ehr.contracts import OPERATIONS, EHRResult, Fault
from kyron_eval.models import Booking, Review, SystemState, ToolBehavior

SYNTHETIC_DOB = "1980-01-02"
SYNTHETIC_PHONE = "2025550100"


class MockEHRAdapter:
    name = "mock"

    def __init__(self, state: SystemState, behavior: ToolBehavior, *, faults=None):
        self.state = state.model_copy(deep=True)
        self.initial = state.model_copy(deep=True)
        self.behavior = behavior.model_copy(deep=True)
        self.faults = dict(faults or {})
        if set(self.faults) - set(OPERATIONS):
            raise ValueError("Unknown EHR fault operation")
        if set(self.faults.values()) - set(get_args(Fault)):
            raise ValueError("Unknown EHR fault mode")
        self.journal: list[dict] = []
        self.idempotent_results: dict[str, tuple[dict, EHRResult]] = {}

    def capabilities(self):
        return {
            "adapter": self.name,
            "version": "mock-ehr-1.0",
            "synthetic": True,
            "connection": "local",
            "operations": {op: True for op in OPERATIONS},
            "persistence": "run-scoped mock snapshots; not a real EHR",
        }

    def result(
        self, operation, *, status="OK", data=None, code=None, authoritative=False, observed_at=None
    ):
        result = EHRResult(
            operation=operation,
            status=status,
            data=data or {},
            failure_code=code,
            authoritative=authoritative,
            observed_at=observed_at,
            source=self.name,
        )
        self.journal.append(result.model_dump(mode="json"))
        return result

    def failure(self, operation):
        fault = self.faults.get(operation)
        if fault == "failure":
            return self.result(operation, status="ERROR", code="MOCK_EHR_UNAVAILABLE")
        if fault in {"timeout", "readback_failure"}:
            return self.result(
                operation,
                status="UNKNOWN",
                code="MOCK_TIMEOUT" if fault == "timeout" else "MOCK_READBACK_UNAVAILABLE",
            )
        return None

    def patient_failure(self, operation, patient_id):
        if patient_id != self.state.patient_id:
            return self.result(operation, status="ERROR", code="PATIENT_MISMATCH")
        return self.failure(operation)

    def get_patient(self, patient_id):
        failed = self.patient_failure("get_patient", patient_id)
        return failed or self.result(
            "get_patient", data={"patient_id": patient_id, "synthetic": True}, authoritative=True
        )

    def verify_identity(self, patient_id, birth_date, phone):
        failed = self.patient_failure("verify_identity", patient_id)
        if failed:
            return failed
        factors = tuple(
            code
            for code, matched in (
                ("BIRTH_DATE", birth_date == SYNTHETIC_DOB),
                ("PHONE", "".join(c for c in phone if c.isdigit())[-10:] == SYNTHETIC_PHONE),
            )
            if matched
        )
        result = IdentityVerification(
            patient_id=patient_id,
            verified=len(factors) == 2,
            verified_factor_codes=factors,
            requires_human_review=len(factors) != 2,
        )
        return self.result(
            "verify_identity", data=result.model_dump(mode="json"), authoritative=True
        )

    def get_appointments(self, patient_id):
        failed = self.patient_failure("get_appointments", patient_id)
        if failed:
            return failed
        stale = self.faults.get("get_appointments") == "stale"
        source = self.initial if stale else self.state
        return self.result(
            "get_appointments",
            status="UNKNOWN" if stale else "OK",
            data={"bookings": [b.model_dump(mode="json") for b in source.bookings]},
            code="STALE_APPOINTMENT_READ" if stale else None,
            authoritative=not stale,
        )

    def get_availability(self, now):
        failed = self.failure("get_availability")
        if failed:
            return failed
        stale = self.faults.get("get_availability") == "stale"
        observed = now - timedelta(hours=1) if stale else now
        expires = (
            now - timedelta(seconds=1)
            if stale
            else now + timedelta(seconds=self.behavior.snapshot_ttl_seconds)
        )
        snapshot = AvailabilitySnapshot(
            snapshot_id="synthetic-snapshot",
            observed_at=observed,
            expires_at=expires,
            slots=tuple(self.state.slots),
        )
        # Return stale evidence explicitly so the existing scheduling validator rejects it.
        return self.result(
            "get_availability",
            data={"snapshot": snapshot.model_dump(mode="json")},
            authoritative=not stale,
            observed_at=observed,
            code="STALE_AVAILABILITY" if stale else None,
        )

    def create_appointment(self, *, patient_id, confirmation, idempotency_key):
        operation = "create_appointment"
        failed = self.patient_failure(operation, patient_id)
        if failed:
            return failed
        request = {
            "patient_id": patient_id,
            "slot": confirmation.offer.slot.model_dump(mode="json"),
            "confirmation_id": confirmation.confirmation_id,
        }
        if idempotency_key in self.idempotent_results:
            old_request, old_result = self.idempotent_results[idempotency_key]
            if old_request != request:
                return self.result(operation, status="ERROR", code="IDEMPOTENCY_CONFLICT")
            return old_result.model_copy(deep=True)
        slot = confirmation.offer.slot
        if slot not in self.state.slots:
            return self.result(operation, status="ERROR", code="SLOT_NO_LONGER_AVAILABLE")
        mode = self.faults.get(operation, self.behavior.booking)
        if mode == "failure":
            return self.result(
                operation,
                status="ERROR",
                code="MOCK_EHR_UNAVAILABLE",
                data=AppointmentMutationResult(
                    operation_id="synthetic-book",
                    completed=False,
                    failure_code="MOCK_EHR_UNAVAILABLE",
                ).model_dump(mode="json"),
            )
        appointment_id = (
            "synthetic-appointment-1"
            if not self.state.bookings
            else f"synthetic-appointment-{len(self.state.bookings) + 1}"
        )
        response = AppointmentMutationResult(
            operation_id="synthetic-book", completed=True, appointment_id=appointment_id
        )
        if mode != "ack_without_commit":
            booked_slot = slot
            if mode == "mismatch":
                booked_slot = next((s for s in self.state.slots if s != slot), slot)
            self.state.bookings.append(
                Booking(
                    appointment_id=appointment_id,
                    patient_id=patient_id,
                    slot=booked_slot,
                    confirmation_event_id=confirmation.confirmation_event_id,
                )
            )
            self.state.slots = [s for s in self.state.slots if s != booked_slot]
        result = self.result(
            operation,
            status="UNKNOWN" if mode == "timeout_after_commit" else "OK",
            code="MOCK_TIMEOUT_AFTER_COMMIT"
            if mode == "timeout_after_commit"
            else "MOCK_ACK_WITHOUT_COMMIT"
            if mode == "ack_without_commit"
            else None,
            data=response.model_dump(mode="json") if mode != "timeout_after_commit" else {},
        )
        self.idempotent_results[idempotency_key] = (request, result)
        return result

    def cancel_appointment(self, patient_id, appointment_id):
        operation = "cancel_appointment"
        failed = self.patient_failure(operation, patient_id)
        if failed:
            return failed
        booking = next(
            (
                b
                for b in self.state.bookings
                if b.appointment_id == appointment_id and b.patient_id == patient_id
            ),
            None,
        )
        if not booking:
            return self.result(operation, status="ERROR", code="APPOINTMENT_NOT_FOUND")
        mode = self.faults.get(operation, "commit")
        if mode != "ack_without_commit":
            self.state.bookings.remove(booking)
            if booking.slot not in self.state.slots:
                self.state.slots.append(booking.slot)
        return self.result(
            operation,
            status="UNKNOWN" if mode == "timeout_after_commit" else "OK",
            data={}
            if mode == "timeout_after_commit"
            else {"completed": True, "appointment_id": appointment_id},
            code="MOCK_TIMEOUT_AFTER_COMMIT" if mode == "timeout_after_commit" else None,
        )

    def get_clinical_context(self, patient_id):
        failed = self.patient_failure("get_clinical_context", patient_id)
        return failed or self.result(
            "get_clinical_context",
            authoritative=True,
            data={
                "patient_id": patient_id,
                "condition_codes": ["synthetic-condition"],
                "medication_codes": [],
                "observation_codes": [],
                "synthetic": True,
            },
        )

    def create_human_review_request(self, patient_id, reason_code):
        operation = "create_human_review_request"
        failed = self.patient_failure(operation, patient_id)
        if failed:
            return failed
        mode = self.faults.get(operation, self.behavior.review)
        if mode == "failure":
            response = RecordResult(
                operation_id="synthetic-review",
                completed=False,
                failure_code="MOCK_REVIEW_UNAVAILABLE",
            )
            return self.result(
                operation,
                status="ERROR",
                code=response.failure_code,
                data=response.model_dump(mode="json"),
            )
        existing = next(
            (
                r
                for r in self.state.reviews
                if r.patient_id == patient_id and r.reason_code == reason_code
            ),
            None,
        )
        record_id = (
            existing.record_id if existing else f"synthetic-review-{len(self.state.reviews) + 1}"
        )
        if mode != "ack_without_commit" and not existing:
            self.state.reviews.append(
                Review(record_id=record_id, patient_id=patient_id, reason_code=reason_code)
            )
        response = RecordResult(
            operation_id="synthetic-review", completed=True, record_reference=record_id
        )
        return self.result(
            operation,
            status="UNKNOWN" if mode == "timeout_after_commit" else "OK",
            code="MOCK_TIMEOUT_AFTER_COMMIT"
            if mode == "timeout_after_commit"
            else "MOCK_ACK_WITHOUT_COMMIT"
            if mode == "ack_without_commit"
            else None,
            data={} if mode == "timeout_after_commit" else response.model_dump(mode="json"),
        )

    def read_state(self, patient_id, now):
        failed = self.patient_failure("read_state", patient_id)
        if failed:
            return failed
        appointments = self.get_appointments(patient_id)
        if not appointments.ok or not appointments.authoritative:
            return self.result("read_state", status="UNKNOWN", code=appointments.failure_code)
        stale = self.faults.get("read_state") == "stale"
        state = self.initial if stale else self.state
        return self.result(
            "read_state",
            status="UNKNOWN" if stale else "OK",
            data={"state": state.model_dump(mode="json")},
            authoritative=not stale,
            observed_at=now - timedelta(hours=1) if stale else now,
            code="STALE_STATE_READ" if stale else None,
        )

    def close(self):
        pass
