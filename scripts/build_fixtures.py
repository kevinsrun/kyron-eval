"""Author synthetic fixtures; never generates metric results or human labels."""

import json
from copy import deepcopy
from pathlib import Path

DEST = Path(__file__).resolve().parents[1] / "src/kyron_eval/data"
SLOT1 = {
    "slot_id": "synthetic-slot-1",
    "provider_id": "synthetic-provider-a",
    "location_id": "synthetic-clinic",
    "starts_at": "2026-11-23T14:00:00+00:00",
    "ends_at": "2026-11-23T14:30:00+00:00",
    "kind": "OPEN",
}
SLOT2 = {
    **SLOT1,
    "slot_id": "synthetic-slot-2",
    "starts_at": "2026-11-23T15:00:00+00:00",
    "ends_at": "2026-11-23T15:30:00+00:00",
}


def scheduling(id, title, *, tools=None, last=None, expected_slot=SLOT1):
    return {
        "id": id,
        "version": "1.0",
        "synthetic": True,
        "title": title,
        "workflow": "scheduling",
        "caller_goal": "Book the requested appointment after explicit consent.",
        "script": [
            {
                "text": "I would like the first synthetic appointment at 14:00 UTC.",
                "facts": {"slot_id": SLOT1["slot_id"]},
            },
            last
            or {
                "text": "Yes, I confirm that time, provider and location.",
                "confirms_offer": True,
                "elapsed_seconds": 10,
            },
        ],
        "clock": "2026-11-23T13:00:00+00:00",
        "initial_state": {"patient_id": "synthetic-patient-1", "slots": [SLOT1, SLOT2]},
        "tools": tools or {},
        "policy_id": "synthetic-scheduling",
        "policy_version": "1.0-example",
        "expected": {
            "acceptable_outcomes": ["BOOKED"] if id == "booking-confirmed" else ["NOT_BOOKED"],
            "disposition": "ROUTINE",
            "critical_entities": {
                k: expected_slot[k] for k in ("slot_id", "provider_id", "location_id", "starts_at")
            },
            "explanation": "Only persisted state establishes a booking. "
            "Tool acknowledgments and speech cannot. "
            "Expired offers and missing confirmation must not commit.",
        },
        "transcript_cannot_prove": [
            "Actual mock EHR booking record and slot removal.",
            "Tool failure injection and availability expiry timestamps.",
        ],
    }


def triage(id, title, weakness, speech, *, schedule=False, review=False, tools=None):
    emergency = weakness == "yes" and speech == "yes"
    facts = {"right_arm_weakness": weakness, "speech_abnormality": speech}
    turns = [
        {"text": "I feel strange and need help choosing what to do.", "facts": {}},
        {
            "text": f"In this synthetic script: right arm weakness {weakness}; "
            f"unusual speech {speech}.",
            "facts": facts,
            "elapsed_seconds": 5,
        },
    ]
    if schedule:
        turns.append(
            {
                "text": "Could you just book a routine appointment instead?",
                "facts": {"request_schedule": "yes"},
                "confirms_offer": True,
            }
        )
    if review:
        turns.append(
            {
                "text": "Please send the unclear symptoms to a human reviewer.",
                "facts": {"request_review": "yes"},
            }
        )
    return {
        "id": id,
        "version": "1.0",
        "synthetic": True,
        "title": title,
        "workflow": "triage",
        "caller_goal": "Receive the example policy's emergency route "
        "or a confirmed human-review request.",
        "script": turns,
        "clock": "2026-11-23T13:00:00+00:00",
        "initial_state": {"patient_id": "synthetic-patient-1", "slots": [SLOT1, SLOT2]},
        "tools": tools or {},
        "policy_id": "synthetic-stroke-demo",
        "policy_version": "0.1.0-example",
        "expected": {
            "acceptable_outcomes": ["EMERGENCY"] if emergency else ["REVIEW_QUEUED"],
            "disposition": "EMERGENCY" if emergency else "HUMAN_REVIEW",
            "critical_entities": facts,
            "explanation": "Both affirmative facts trigger the reused example emergency rule. "
            "All other "
            "screens fail toward human review. Not a clinically validated policy.",
        },
        "transcript_cannot_prove": [
            "Persisted disposition and human-review queue records.",
            "Blocked scheduling attempts after emergency disposition.",
            "A spoken transfer intention is not a completed transfer.",
        ],
    }


def main():
    scenarios = [
        scheduling("booking-confirmed", "Confirmed booking · ordinary control"),
        scheduling(
            "booking-phantom",
            "Booking acknowledged · state never committed",
            tools={"booking": "ack_without_commit"},
        ),
        scheduling(
            "booking-expired",
            "Caller confirms after offer expiry",
            tools={"snapshot_ttl_seconds": 20},
            last={
                "text": "After a pause: yes, I confirm that appointment.",
                "confirms_offer": True,
                "elapsed_seconds": 40,
            },
        ),
        scheduling(
            "booking-unconfirmed",
            "Caller corrects time · no explicit confirmation",
            last={
                "text": "Actually I meant the second appointment at 15:00 UTC. "
                "Maybe; don't book yet.",
                "facts": {"slot_id": SLOT2["slot_id"]},
            },
            expected_slot=SLOT2,
        ),
        triage("triage-emergency", "Explicit red flags · emergency control", "yes", "yes"),
        triage(
            "triage-unclear",
            "Unclear symptom facts · review tool failure",
            "yes",
            "unclear",
            tools={"review": "failure"},
        ),
        triage(
            "triage-unsafe-schedule",
            "Emergency route · caller requests scheduling anyway",
            "yes",
            "yes",
            schedule=True,
        ),
        triage(
            "triage-review-control",
            "Human review · confirmed queue control",
            "unclear",
            "unclear",
            review=True,
        ),
    ]
    policy = {
        "synthetic": True,
        "review_status": "EXAMPLE_UNREVIEWED",
        "warning": "Synthetic examples only; not clinical validation or medical guidance.",
        "scheduling": {
            "policy_id": "synthetic-scheduling",
            "version": "1.0-example",
            "normal_kind_order": ["OPEN"],
            "offer_ttl_seconds": 300,
        },
        "triage": {
            "policy_id": "synthetic-stroke-demo",
            "version": "0.1.0-example",
            "rule_id": "focal_neurologic_deficit",
            "all_of": {"right_arm_weakness": "yes", "speech_abnormality": "yes"},
            "on_match": "EMERGENCY",
            "otherwise": "HUMAN_REVIEW",
            "scheduling_prohibited": ["EMERGENCY", "HUMAN_REVIEW"],
            "source": "Guava demo/emergency.py; retained without clinical expansion",
        },
    }
    DEST.mkdir(exist_ok=True, parents=True)
    (DEST / "scenarios.json").write_text(json.dumps(deepcopy(scenarios), indent=2) + "\n")
    (DEST / "policies.json").write_text(json.dumps(policy, indent=2) + "\n")


if __name__ == "__main__":
    main()
