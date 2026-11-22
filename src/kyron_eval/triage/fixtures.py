"""Fictional operator scripts; expected labels are synthetic, never physician references."""

import re

from kyron_eval.triage.datasets import CARE_LABELS

DEMOS = {
    "nonurgent": {
        "title": "Nonurgent appointment request",
        "script": [
            "I have a mild itchy patch on my arm for two weeks. It is not worsening. "
            "I would like a routine appointment to have it checked."
        ],
        "expected_action": CARE_LABELS[2],
    },
    "urgent": {
        "title": "Urgent escalation",
        "script": ["I have severe chest pain and trouble breathing that started ten minutes ago."],
        "expected_action": CARE_LABELS[3],
    },
    "insufficient": {
        "title": "Insufficient information, then clarification",
        "script": [
            "I do not feel well.",
            "I have a mild itchy patch on my arm for two weeks. I want a routine appointment.",
        ],
        "expected_action": CARE_LABELS[0],
        "note": "Initial-prefix expectation only; later disclosures require a new reference.",
    },
}


def demo_reference(demo_id):
    if demo_id not in DEMOS:
        return None
    return {
        "action": DEMOS[demo_id]["expected_action"],
        "source": "Kyron fictional demonstration fixture",
        "annotation_type": "SYNTHETIC_ENGINEERING_EXPECTATION",
        "independent_clinician_label": False,
        "scope": "Initial scripted prefix only; not a benchmark case or clinical truth",
    }


def compare_demo(demo_id, encounter):
    """Exact scripted prefix matching only; changed live wording is UNKNOWN."""
    prediction = encounter.prediction
    if demo_id not in DEMOS or not prediction:
        return {"status": "UNKNOWN", "reason": "No supported synthetic reference/prediction pair"}

    def normalize(text):
        return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()

    ids = prediction.get("source_event_ids", [])
    context = " ".join(encounter.turns.get(i, "") for i in ids)
    if normalize(context) != normalize(DEMOS[demo_id]["script"][0]):
        return {
            "status": "UNKNOWN",
            "reason": "Actual caller prefix differs from the synthetic reference script",
        }
    action = prediction.get("action")
    known = action in CARE_LABELS
    return {
        "status": ("PASS" if action == DEMOS[demo_id]["expected_action"] else "FAIL")
        if known
        else "UNKNOWN",
        "reason": "Exact-script synthetic reference agreement; not clinical accuracy",
        "reference_source": "SYNTHETIC_ENGINEERING_EXPECTATION",
        "urgency_mismatch": action != CARE_LABELS[3]
        if DEMOS[demo_id]["expected_action"] == CARE_LABELS[3] and known
        else None,
        "evaluator_version": "synthetic-prefix-agreement-1.0",
    }
