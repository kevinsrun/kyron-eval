"""Deterministic agents interact with guarded tools, never with evaluator expectations."""

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from clinical_triage.demo.emergency import evaluate_stroke_red_flag
from clinical_triage.domain.conversation import SessionPhase, SessionState
from clinical_triage.domain.disposition import Disposition
from clinical_triage.domain.policy import (
    FactDataType,
    FactDefinition,
    FactPredicate,
    PolicyBundle,
    PolicyReviewStatus,
    PredicateOperator,
    RuleSpec,
)
from clinical_triage.domain.scheduling import AvailabilitySnapshot
from clinical_triage.scheduling import (
    SchedulingPolicy,
    SchedulingPolicyError,
    build_confirmation,
    rank_appointment_options,
)
from kyron_eval.ehr import MockEHRAdapter
from kyron_eval.ehr.mock import SYNTHETIC_DOB, SYNTHETIC_PHONE
from kyron_eval.models import Event, Scenario, SystemState, Trace

DATA = Path(__file__).parent / "data"
AGENT_VERSIONS = {"A": "baseline-1.0", "B": "verified-ehr-2.1", "LLM": "llm-text-1.0"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def trace_hash(trace: Trace) -> str:
    # Hash fields actually persisted, excluding defaults introduced by a newer reader schema.
    return digest(trace.model_dump(mode="json", exclude_unset=True))


def load_scenarios() -> list[Scenario]:
    scenarios = [
        Scenario.model_validate(s) for s in json.loads((DATA / "scenarios.json").read_text())
    ]
    if len({s.id for s in scenarios}) != len(scenarios) or not scenarios:
        raise ValueError("Dataset must be nonempty with unique scenario IDs")
    return scenarios


class MockWorld:
    """A private state copy and identical injection configuration per replay.

    Tools produce evidence; an invocation or acknowledgment cannot establish a commit.
    Every attempted invalid action crosses the original domain validators.
    """

    def __init__(self, scenario: Scenario, variant: str, *, adapter=None, faults=None):
        self.adapter = adapter or MockEHRAdapter(
            scenario.initial_state, scenario.tools, faults=faults
        )
        self.state = (
            self.adapter.state
            if isinstance(self.adapter, MockEHRAdapter)
            else scenario.initial_state.model_copy(deep=True)
        )
        self.behavior = scenario.tools.model_copy(deep=True)
        self.workflow = scenario.workflow
        self.policy_facts = None
        self.now = scenario.clock
        self.facts: dict[str, str] = {}
        self.last_caller: Event | None = None
        self.confirmation = None
        self.offer = None
        self.offer_created_seq = 0
        policy = json.loads((DATA / "policies.json").read_text())
        self.scheduling_policy = SchedulingPolicy.model_validate(policy["scheduling"])
        raw_triage = policy["triage"]
        if (
            raw_triage["all_of"] != {"right_arm_weakness": "yes", "speech_abnormality": "yes"}
            or raw_triage["rule_id"] != "focal_neurologic_deficit"
            or raw_triage["on_match"] != "EMERGENCY"
            or raw_triage["otherwise"] != "HUMAN_REVIEW"
            or set(raw_triage["scheduling_prohibited"]) != {"EMERGENCY", "HUMAN_REVIEW"}
        ):
            raise ValueError("The reused demo rule only supports the documented two-fact policy")
        self.triage_policy = PolicyBundle(
            policy_id=raw_triage["policy_id"],
            version=raw_triage["version"],
            checksum_sha256=digest(policy),
            effective_date=date(2026, 11, 22),
            review_status=PolicyReviewStatus(policy["review_status"]),
            fact_definitions=tuple(
                FactDefinition(fact_id=key, data_type=FactDataType.CODE)
                for key in raw_triage["all_of"]
            ),
            rules=(
                RuleSpec(
                    rule_id=raw_triage["rule_id"],
                    priority=0,
                    disposition=Disposition.EMERGENCY,
                    all_of=tuple(
                        FactPredicate(fact_id=k, operator=PredicateOperator.EQUALS, value=v)
                        for k, v in raw_triage["all_of"].items()
                    ),
                ),
            ),
        )
        active = self.scheduling_policy if scenario.workflow == "scheduling" else self.triage_policy
        if (scenario.policy_id, scenario.policy_version) != (active.policy_id, active.version):
            raise ValueError("Scenario policy identity/version does not match the active policy")
        self.trace = Trace(
            schema_version="2.0",
            harness_version="ehr-replay-2.0",
            scenario_id=scenario.id,
            scenario_version=scenario.version,
            scenario_sha256=digest(scenario.model_dump(mode="json")),
            variant=variant,
            agent_version=AGENT_VERSIONS[variant],
            policy_id=scenario.policy_id,
            policy_version=scenario.policy_version,
            policy_sha256=digest(policy),
            state_before=self.state.model_copy(deep=True),
            ehr={"capabilities": self.adapter.capabilities()},
        )

    def ehr_call(self, operation: str, **inputs):
        before = self.state.model_dump(mode="json")
        result = getattr(self.adapter, operation)(**inputs)
        safe_inputs = {
            k: v.model_dump(mode="json")
            if hasattr(v, "model_dump")
            else v.isoformat()
            if hasattr(v, "isoformat")
            else v
            for k, v in inputs.items()
        }
        if result.failure_code:
            self.event(
                "error",
                name=result.failure_code,
                origin="tool_uncertainty"
                if result.status == "UNKNOWN"
                else "unsupported_operation"
                if result.status == "UNSUPPORTED"
                else "tool_failure",
                tool=operation,
            )
        self.event(
            "tool",
            name=f"ehr.{operation}",
            inputs=safe_inputs,
            result=result.model_dump(mode="json"),
            state_before=before,
            state_after=self.state.model_dump(mode="json"),
            state_source="mock_internal" if self.adapter.name == "mock" else "policy_cache",
        )
        return result

    def prepare(self):
        patient = self.ehr_call("get_patient", patient_id=self.state.patient_id)
        identity = self.ehr_call(
            "verify_identity",
            patient_id=self.state.patient_id,
            birth_date=SYNTHETIC_DOB,
            phone=SYNTHETIC_PHONE,
        )
        self.ehr_call("get_appointments", patient_id=self.state.patient_id)
        self.ehr_call("get_clinical_context", patient_id=self.state.patient_id)
        self.identity_verified = bool(
            patient.ok and identity.ok and identity.data.get("verified") is True
        )
        self.trace.ehr["identity_verified"] = self.identity_verified
        self.trace.ehr["identity_result"] = identity.model_dump(mode="json")
        self.trace.ehr["initial_appointments"] = next(
            (e.data["result"] for e in self.trace.events if e.name == "ehr.get_appointments"), None
        )

    def finish(self):
        final = self.ehr_call("read_state", patient_id=self.state.patient_id, now=self.now)
        self.trace.ehr["final_readback"] = final.model_dump(mode="json")
        self.trace.state_after = None
        if final.ok and final.authoritative and final.data.get("state"):
            payload = {**final.data["state"], "disposition": self.state.disposition.value}
            self.trace.state_after = SystemState.model_validate(payload)
        if isinstance(self.adapter, MockEHRAdapter):
            self.trace.ehr["mock_storage"] = {
                "evidence_class": "PRIVILEGED_SYNTHETIC_MOCK_STORAGE",
                "warning": (
                    "Mock persistence only; unavailable readback remains UNKNOWN to evaluators."
                ),
                "state": self.adapter.state.model_dump(mode="json"),
                "journal": self.adapter.journal,
            }
        self.trace.final_disposition = self.state.disposition
        self.event("state", name="ehr.final_readback", result=final.model_dump(mode="json"))
        self.event("disposition", disposition=self.state.disposition.value)
        self.adapter.close()
        return self.trace

    def event(
        self, kind: str, *, text: str | None = None, name: str | None = None, **data: Any
    ) -> Event:
        seq = len(self.trace.events) + 1
        event = Event(
            id=f"e{seq:04d}", seq=seq, at=self.now, kind=kind, text=text, name=name, data=data
        )
        self.trace.events.append(event)
        return event

    def say(self, text: str):
        self.event("agent", text=text)

    def tool(self, name: str, **inputs: Any) -> dict[str, Any]:
        before = self.state.model_dump(mode="json")
        try:
            result = getattr(self, f"_{name}")(**inputs)
        except (ValidationError, SchedulingPolicyError) as exc:
            code = (
                exc.reason_code if isinstance(exc, SchedulingPolicyError) else "DOMAIN_VALIDATION"
            )
            result = {"completed": False, "failure_code": code}
            self.event(
                "error",
                name=code,
                origin="tool_failure"
                if name == "search" and ("STALE" in code or "SNAPSHOT_EXPIRED" in code)
                else "policy_rejection",
                tool=name,
                explanation=str(exc),
            )
        self.event(
            "tool",
            name=name,
            inputs=inputs,
            result=result,
            state_before=before,
            state_after=self.state.model_dump(mode="json"),
        )
        return result

    def _route(self, **facts):
        actual = {k: self.facts.get(k) for k in ("right_arm_weakness", "speech_abnormality")}
        if facts != actual:
            raise SchedulingPolicyError("FACT_EVIDENCE_MISMATCH")
        triage = evaluate_stroke_red_flag(**facts)
        # Retain the deterministic example rule; bind the actual policy manifest checksum.
        triage = type(triage).model_validate(
            {
                **triage.model_dump(),
                "policy_checksum_sha256": self.trace.policy_sha256,
                "policy_id": self.triage_policy.policy_id,
                "policy_version": self.triage_policy.version,
            }
        )
        self.state.disposition = triage.disposition
        self.policy_facts = actual
        self.event("policy", name="synthetic-stroke-demo", **triage.model_dump(mode="json"))
        return triage.model_dump(mode="json")

    def _search(self):
        self._require_current_policy()
        availability = self.ehr_call("get_availability", now=self.now)
        if not availability.ok or not availability.data.get("snapshot"):
            return {
                "completed": False,
                "failure_code": availability.failure_code,
                "ehr_status": availability.status,
            }
        snapshot = AvailabilitySnapshot.model_validate(availability.data["snapshot"])
        options = rank_appointment_options(
            snapshot=snapshot,
            disposition=self.state.disposition,
            policy=self.scheduling_policy,
            now=self.now,
        )
        desired = self.facts.get("slot_id")
        offers = options.normal_availability + options.alternative_availability
        self.offer = next((o for o in offers if o.slot.slot_id == desired), None)
        if self.offer is None and offers:
            self.offer = offers[0]
        if self.offer is None:
            return {"completed": False, "failure_code": "NO_AVAILABILITY"}
        SessionState(
            call_id="synthetic-replay",
            phase=SessionPhase.SLOT_OFFERED,
            disposition=self.state.disposition,
            offered_slot_id=self.offer.slot.slot_id,
        )
        self.offer_created_seq = len(self.trace.events) + 1
        return {"completed": True, "offer": self.offer.model_dump(mode="json")}

    def _book(self, patient_id: str, slot_id: str):
        self._require_current_policy()
        # Guard even when the agent attempts to skip the normal search path.
        SessionState(
            call_id="synthetic-replay",
            phase=SessionPhase.BOOKING_COMMIT,
            disposition=self.state.disposition,
            offered_slot_id=slot_id,
        )
        if not self.offer or self.offer.slot.slot_id != slot_id:
            raise SchedulingPolicyError("NO_MATCHING_OFFER")
        if patient_id != self.state.patient_id:
            raise SchedulingPolicyError("PATIENT_MISMATCH")
        if not getattr(self, "identity_verified", False):
            raise SchedulingPolicyError("IDENTITY_NOT_VERIFIED")
        if not self.last_caller or not self.last_caller.data["confirms_offer"]:
            raise SchedulingPolicyError("EXPLICIT_CONFIRMATION_REQUIRED")
        if self.last_caller.seq <= self.offer_created_seq:
            raise SchedulingPolicyError("CONFIRMATION_PREDATES_OFFER")
        confirmation = build_confirmation(
            offer=self.offer,
            confirmation_id="synthetic-confirmation",
            confirmation_event_id=self.last_caller.id,
            confirmed_at=self.now,
        )
        if self.now >= self.offer.slot.starts_at:
            raise SchedulingPolicyError("SLOT_ALREADY_STARTED")
        if slot_id != self.facts.get("slot_id"):
            raise SchedulingPolicyError("REQUEST_ENTITY_MISMATCH")
        if not any(s.slot_id == slot_id for s in self.state.slots):
            raise SchedulingPolicyError("SLOT_NO_LONGER_AVAILABLE")
        result = self.ehr_call(
            "create_appointment",
            patient_id=patient_id,
            confirmation=confirmation,
            idempotency_key=f"booking-{slot_id}",
        )
        return {
            **result.data,
            "completed": result.data.get("completed")
            if result.ok
            else None
            if result.status == "UNKNOWN"
            else False,
            "failure_code": result.failure_code
            if not result.ok
            else result.data.get("failure_code"),
            "ehr_status": result.status,
        }

    def _read_state(self):
        result = self.ehr_call("read_state", patient_id=self.state.patient_id, now=self.now)
        verified = result.ok and result.authoritative
        state = result.data.get("state") if verified else None
        appointments = result.data.get("appointments", {})
        bookings = (
            state.get("bookings")
            if state is not None
            else appointments.get("data", {}).get("bookings")
            if appointments.get("status") == "OK" and appointments.get("authoritative")
            else None
        )
        return {
            "completed": verified,
            "state": state,
            "bookings": bookings,
            "ehr_status": result.status,
            "failure_code": result.failure_code,
        }

    def _request_review(self, reason_code: str):
        if not self.identity_verified:
            raise SchedulingPolicyError("IDENTITY_NOT_VERIFIED")
        result = self.ehr_call(
            "create_human_review_request", patient_id=self.state.patient_id, reason_code=reason_code
        )
        return {
            **result.data,
            "completed": result.data.get("completed")
            if result.ok
            else None
            if result.status == "UNKNOWN"
            else False,
            "failure_code": result.failure_code
            if not result.ok
            else result.data.get("failure_code"),
            "ehr_status": result.status,
        }

    def _require_current_policy(self):
        actual = {k: self.facts.get(k) for k in ("right_arm_weakness", "speech_abnormality")}
        if self.workflow == "triage" and self.policy_facts != actual:
            raise SchedulingPolicyError("CURRENT_TRIAGE_POLICY_REQUIRED")

    def _get_patient(self, patient_id):
        return self.ehr_call("get_patient", patient_id=patient_id).model_dump(mode="json")

    def _verify_identity(self, patient_id, birth_date, phone):
        result = self.ehr_call(
            "verify_identity", patient_id=patient_id, birth_date=birth_date, phone=phone
        )
        self.identity_verified = result.ok and result.data.get("verified") is True
        self.trace.ehr["identity_verified"] = self.identity_verified
        self.trace.ehr["identity_result"] = result.model_dump(mode="json")
        return result.model_dump(mode="json")

    def _get_appointments(self, patient_id):
        return self.ehr_call("get_appointments", patient_id=patient_id).model_dump(mode="json")

    def _get_clinical_context(self, patient_id):
        return self.ehr_call("get_clinical_context", patient_id=patient_id).model_dump(mode="json")

    def _cancel(self, patient_id, appointment_id):
        self._require_current_policy()
        if self.state.disposition.value in {"EMERGENCY", "HUMAN_REVIEW"}:
            raise SchedulingPolicyError("DISPOSITION_PROHIBITS_CANCELLATION")
        if patient_id != self.state.patient_id or not self.identity_verified:
            raise SchedulingPolicyError("IDENTITY_NOT_VERIFIED")
        if (
            not self.last_caller
            or not self.last_caller.data.get("confirms_cancellation")
            or self.facts.get("cancel_appointment_id") != appointment_id
        ):
            raise SchedulingPolicyError("EXPLICIT_CANCELLATION_CONFIRMATION_REQUIRED")
        result = self.ehr_call(
            "cancel_appointment", patient_id=patient_id, appointment_id=appointment_id
        )
        return {
            **result.data,
            "ehr_status": result.status,
            "failure_code": result.failure_code,
            "completed": None if result.status == "UNKNOWN" else result.ok,
        }


def replay(scenario: Scenario, variant: str, *, adapter=None, faults=None) -> Trace:
    world = MockWorld(scenario, variant, adapter=adapter, faults=faults)
    world.prepare()
    # Only caller observations, tool responses and state reads inform agent behavior.
    # scenario.expected is never consulted by either agent.
    for index, turn in enumerate(scenario.script):
        world.now += timedelta(seconds=turn.elapsed_seconds)
        world.last_caller = world.event(
            "caller", text=turn.text, facts=turn.facts, confirms_offer=turn.confirms_offer
        )
        world.facts.update(turn.facts)
        if index == 0 and scenario.workflow == "scheduling":
            search = world.tool("search")
            if search.get("offer"):
                slot = world.offer.slot
                world.say(
                    f"I can offer {slot.slot_id} at {slot.starts_at.isoformat()} with "
                    f"{slot.provider_id}, {slot.location_id}. Do you confirm this appointment?"
                )
    if scenario.workflow == "triage":
        _triage_agent(world, variant)
    else:
        _scheduling_agent(world, variant)
    return world.finish()


def _scheduling_agent(world: MockWorld, variant: str):
    if variant == "B" and not world.identity_verified:
        world.say(
            "I could not verify your identity. No booking was attempted. "
            "Please call the clinic to verify your identity before scheduling."
        )
        return
    if variant == "B" and world.offer and world.offer.slot.slot_id != world.facts.get("slot_id"):
        world.tool("search")  # Re-read after a caller correction; never treat it as consent.
        if world.offer:
            refreshed = world.offer.slot
            world.say(
                f"The updated offer is {refreshed.slot_id} at {refreshed.starts_at.isoformat()} "
                f"with {refreshed.provider_id}, {refreshed.location_id}."
            )
    slot = world.offer.slot if world.offer else None
    if slot is None:
        world.say(
            "I could not find a verified appointment. Please call the clinic to choose a slot."
        )
        return
    if variant == "B" and (
        not world.last_caller.data["confirms_offer"]
        or world.last_caller.seq <= world.offer_created_seq
    ):
        world.say(
            f"No appointment has been booked. Your updated request is {world.facts['slot_id']}. "
            "Please explicitly confirm the offered time before we book."
        )
        return
    if variant == "B" and world.now > world.offer.expires_at:
        world.say(
            "No appointment has been booked. The offer expired. Please request fresh "
            "availability and confirm a new offer before booking."
        )
        return
    mutation = world.tool("book", patient_id=world.state.patient_id, slot_id=slot.slot_id)
    verified = False
    if variant == "B":
        readback = world.tool("read_state")
        observed_bookings = readback["bookings"]
        if observed_bookings is None:
            world.say(
                "The booking status is unknown because EHR readback is unavailable. "
                "Please call the clinic to check before retrying; a booking may exist."
            )
            return
        verified = any(
            b["slot"] == slot.model_dump(mode="json")
            and b["patient_id"] == world.state.patient_id
            and b not in world.trace.state_before.model_dump(mode="json")["bookings"]
            and (
                not mutation.get("appointment_id")
                or b["appointment_id"] == mutation["appointment_id"]
            )
            and b["confirmation_event_id"] == world.last_caller.id
            for b in observed_bookings
        )
        initial = (
            world.trace.ehr.get("initial_appointments", {}).get("data", {}).get("bookings", [])
        )
        if not verified and any(b not in initial for b in observed_bookings):
            world.say(
                "An appointment was saved with details that do not match your request. "
                "I cannot confirm the requested booking. Please call the clinic before retrying."
            )
            return
        if not verified and mutation.get("ehr_status") == "UNSUPPORTED":
            world.say(
                "This EHR connection does not support booking appointments. "
                "The requested appointment was not saved. Please call the clinic to arrange it."
            )
            return
    if variant == "A" or verified:
        world.say(
            f"Your appointment is booked for {slot.slot_id} at {slot.starts_at.isoformat()} "
            f"with {slot.provider_id}, {slot.location_id}. You're all set."
        )
    else:
        world.say(
            "No appointment has been booked: I could not verify a saved booking. "
            "Please call the clinic to arrange an appointment; do not assume a time is reserved."
        )


def _triage_agent(world: MockWorld, variant: str):
    route = world.tool(
        "route",
        right_arm_weakness=world.facts.get("right_arm_weakness"),
        speech_abnormality=world.facts.get("speech_abnormality"),
    )
    if route["disposition"] == "EMERGENCY":
        world.say(
            "This synthetic policy requires emergency escalation. Call 911 now or have "
            "someone call for you. Do not drive yourself. I cannot connect an emergency call."
        )
        if variant == "A" and world.facts.get("request_schedule") == "yes":
            world.tool("search")
            slot = world.state.slots[0]
            world.tool("book", patient_id=world.state.patient_id, slot_id=slot.slot_id)
            world.say(f"Your appointment is booked for {slot.slot_id}. You're all set.")
        return
    if variant == "A" and world.facts.get("request_review") != "yes":
        world.tool("search")
        world.say("Your appointment is booked for synthetic-slot-1. You're all set.")
        return
    world.tool("request_review", reason_code="INCOMPLETE_FACTS")
    verified = True
    if variant == "B":
        observed = world.tool("read_state")["state"]
        if observed is None:
            world.say(
                "The review request status is unknown because EHR readback is unavailable. "
                "Please call the clinic directly to check; I cannot confirm a handoff."
            )
            return
        verified = bool(observed["reviews"])
    if verified:
        world.say(
            "Your request is queued for clinic staff review. A clinician still needs to "
            "review these symptoms; no appointment is booked. Please call the clinic "
            "for next steps. I cannot promise when staff will respond."
        )
    else:
        world.say(
            "I could not send your review request. No handoff or booking is complete. "
            "Please call the clinic directly for human review; the symptom facts remain unclear."
        )
