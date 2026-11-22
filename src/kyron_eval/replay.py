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
from clinical_triage.domain.ehr import RecordResult
from clinical_triage.domain.policy import (
    FactDataType,
    FactDefinition,
    FactPredicate,
    PolicyBundle,
    PolicyReviewStatus,
    PredicateOperator,
    RuleSpec,
)
from clinical_triage.domain.scheduling import AppointmentMutationResult, AvailabilitySnapshot
from clinical_triage.scheduling import (
    SchedulingPolicy,
    SchedulingPolicyError,
    build_confirmation,
    rank_appointment_options,
)
from kyron_eval.models import Booking, Event, Review, Scenario, Trace

DATA = Path(__file__).parent / "data"
AGENT_VERSIONS = {"A": "baseline-1.0", "B": "verified-1.1"}


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

    def __init__(self, scenario: Scenario, variant: str):
        self.state = scenario.initial_state.model_copy(deep=True)
        self.behavior = scenario.tools.model_copy(deep=True)
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
            harness_version="replay-1.1",
            scenario_id=scenario.id,
            scenario_version=scenario.version,
            scenario_sha256=digest(scenario.model_dump(mode="json")),
            variant=variant,
            agent_version=AGENT_VERSIONS[variant],
            policy_id=scenario.policy_id,
            policy_version=scenario.policy_version,
            policy_sha256=digest(policy),
            state_before=self.state.model_copy(deep=True),
        )

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
                "error", name=code, origin="policy_rejection", tool=name, explanation=str(exc)
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
        self.event("policy", name="synthetic-stroke-demo", **triage.model_dump(mode="json"))
        return triage.model_dump(mode="json")

    def _search(self):
        snapshot = AvailabilitySnapshot(
            snapshot_id="synthetic-snapshot",
            observed_at=self.now,
            expires_at=self.now + timedelta(seconds=self.behavior.snapshot_ttl_seconds),
            slots=tuple(self.state.slots),
        )
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
        if self.behavior.booking == "failure":
            result = AppointmentMutationResult(
                operation_id="synthetic-book", completed=False, failure_code="MOCK_EHR_UNAVAILABLE"
            )
            self.event("error", name="MOCK_EHR_UNAVAILABLE", origin="tool_failure", tool="book")
        else:
            appointment_id = "synthetic-appointment-1"
            result = AppointmentMutationResult(
                operation_id="synthetic-book", completed=True, appointment_id=appointment_id
            )
            if self.behavior.booking == "commit":
                self.state.bookings.append(
                    Booking(
                        appointment_id=appointment_id,
                        patient_id=patient_id,
                        slot=self.offer.slot,
                        confirmation_event_id=confirmation.confirmation_event_id,
                    )
                )
                self.state.slots = [s for s in self.state.slots if s.slot_id != slot_id]
            else:
                self.event(
                    "error",
                    name="MOCK_ACK_WITHOUT_COMMIT",
                    origin="tool_failure",
                    tool="book",
                    explanation="Injected acknowledgment without a state commit.",
                )
        return result.model_dump(mode="json")

    def _read_state(self):
        return {"completed": True, "state": self.state.model_dump(mode="json")}

    def _request_review(self, reason_code: str):
        if self.behavior.review == "failure":
            self.event(
                "error",
                name="MOCK_REVIEW_UNAVAILABLE",
                origin="tool_failure",
                tool="request_review",
            )
            result = RecordResult(
                operation_id="synthetic-review",
                completed=False,
                failure_code="MOCK_REVIEW_UNAVAILABLE",
            )
        else:
            result = RecordResult(
                operation_id="synthetic-review",
                completed=True,
                record_reference="synthetic-review-1",
            )
            if self.behavior.review == "commit":
                self.state.reviews.append(
                    Review(
                        record_id="synthetic-review-1",
                        patient_id=self.state.patient_id,
                        reason_code=reason_code,
                    )
                )
            else:
                self.event(
                    "error",
                    name="MOCK_ACK_WITHOUT_COMMIT",
                    origin="tool_failure",
                    tool="request_review",
                    explanation="Injected acknowledgment without a queue record.",
                )
        return result.model_dump(mode="json")


def replay(scenario: Scenario, variant: str) -> Trace:
    world = MockWorld(scenario, variant)
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
    world.trace.state_after = world.state.model_copy(deep=True)
    world.trace.final_disposition = world.state.disposition
    world.event("disposition", disposition=world.state.disposition.value)
    return world.trace


def _scheduling_agent(world: MockWorld, variant: str):
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
    world.tool("book", patient_id=world.state.patient_id, slot_id=slot.slot_id)
    verified = False
    if variant == "B":
        observed = world.tool("read_state")["state"]
        verified = any(
            b["slot"]["slot_id"] == slot.slot_id and b["patient_id"] == world.state.patient_id
            for b in observed["bookings"]
        )
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
        verified = bool(world.tool("read_state")["state"]["reviews"])
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
