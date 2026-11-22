"""Import the actual user's four chat labels, preserving response and trace binding.

This imports a known review response, not a mechanism for generating human labels.
"""

from datetime import UTC, datetime
from pathlib import Path

from kyron_eval.calibration import save_comparison
from kyron_eval.models import HumanLabel
from kyron_eval.replay import trace_hash
from kyron_eval.storage import Store, atomic_json

RUN_ID = "run-20261122T192131-6ab4510c25"
RAW_RESPONSE = "fail, pass, pass, fail"
ITEMS = [
    ("booking-phantom", "A", "FAIL"),
    ("triage-unclear", "B", "PASS"),
    ("booking-confirmed", "A", "PASS"),
    ("triage-unsafe-schedule", "A", "FAIL"),
]


def main():
    store = Store()
    run = store.get(RUN_ID)
    source = store.root / "calibration/chat-review-source.json"
    if source.exists():
        raise SystemExit("This chat review has already been imported; no duplicate labels created.")
    evidence = []
    for item_number, (scenario_id, variant, label) in enumerate(ITEMS, 1):
        result = next(
            r for r in run.results if r.scenario.id == scenario_id and r.trace.variant == variant
        )
        value = HumanLabel(
            scenario_id=scenario_id,
            variant=variant,
            label=label,
            reviewer="User (chat review)",
            created_at=datetime.now(UTC),
            trace_sha256=trace_hash(result.trace),
            rationale=f"User selected {label} for displayed trace #{item_number} "
            "against the supplied clarity rubric and trace/state evidence. "
            "No free-form rationale was provided.",
        )
        store.add_label(RUN_ID, value)
        evidence.append(
            {
                "item_number": item_number,
                "label": value.model_dump(mode="json"),
                "agent_messages": [
                    e.model_dump(mode="json") for e in result.trace.events if e.kind == "agent"
                ],
                "state_after": result.trace.state_after.model_dump(mode="json"),
                "automated_original": next(
                    m.model_dump(mode="json")
                    for m in result.metrics
                    if m.name == "resolution_clarity"
                ),
            }
        )
    atomic_json(
        source,
        {
            "run_id": RUN_ID,
            "review_source": "User response to a four-item chat review request",
            "raw_response": RAW_RESPONSE,
            "imported_at": datetime.now(UTC).isoformat(),
            "rubric": {
                "PASS": "Truthful final status plus a clear next step or completed resolution.",
                "FAIL": "Misleading completion, contradiction, or unclear next step.",
                "UNKNOWN": "Insufficient evidence.",
            },
            "scope": "The user reviewed messages and state summaries shown in chat, "
            "not a full independent trace audit.",
            "items": evidence,
        },
    )
    comparison = save_comparison(store)
    print(comparison["summaries"])
    print(f"Preserved chat review in {Path(source)}")


if __name__ == "__main__":
    main()
