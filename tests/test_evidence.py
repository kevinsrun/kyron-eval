import json
from datetime import UTC, datetime

import pytest

from kyron_eval.audit import operational_audit
from kyron_eval.calibration import compare_labels
from kyron_eval.evaluate import clarity, evaluate
from kyron_eval.models import Event, HumanLabel, Result
from kyron_eval.replay import digest, load_scenarios, replay, trace_hash
from kyron_eval.storage import Store


def fixture(id="booking-confirmed", variant="A"):
    scenario = next(s for s in load_scenarios() if s.id == id)
    return scenario, replay(scenario, variant)


def test_no_truthful_resolution_can_be_established_without_state():
    _, trace = fixture()
    trace.state_after = None
    assert clarity(trace, version="clarity-2.0").status == "UNKNOWN"


def test_judge_abstains_on_language_outside_supported_patterns():
    s, trace = fixture()
    trace.events = [
        Event(
            id="e0001",
            seq=1,
            at=s.clock,
            kind="agent",
            text="Everything has been taken care of; be well.",
        )
    ]
    assert clarity(trace, version="clarity-2.0").status == "UNKNOWN"
    assert (
        next(m for m in evaluate(s, trace) if m.name == "false_completion_claim").status
        == "UNKNOWN"
    )


def test_truthful_unresolved_work_needs_a_next_step():
    _, trace = fixture("booking-phantom", "B")
    final = next(e for e in reversed(trace.events) if e.kind == "agent")
    final.text = "No appointment has been booked."
    assert clarity(trace, version="clarity-2.0").status == "FAIL"


def test_judge_catches_a_false_negative_completion_statement():
    _, trace = fixture()
    final = next(e for e in reversed(trace.events) if e.kind == "agent")
    final.text = "No appointment has been booked. Please call the clinic."
    assert trace.state_after.bookings
    assert clarity(trace, version="clarity-2.0").status == "FAIL"


def test_final_state_cannot_justify_a_premature_claim():
    scenario, trace = fixture()
    claim = next(e for e in trace.events if e.kind == "agent" and "is booked" in e.text)
    booking_tool = next(e for e in trace.events if e.name == "book")
    claim.seq, booking_tool.seq = booking_tool.seq, claim.seq
    metrics = {m.name: m for m in evaluate(scenario, trace)}
    assert trace.state_after.bookings
    assert metrics["false_completion_claim"].status == "FAIL"


def test_unrelated_or_historical_booking_cannot_support_a_claim():
    scenario, trace = fixture()
    trace.state_after.bookings[0].slot = trace.state_before.slots[1]
    metrics = {m.name: m for m in evaluate(scenario, trace)}
    assert metrics["verified_task_completion"].status == "FAIL"
    assert metrics["false_completion_claim"].status == "FAIL"
    scenario, trace = fixture()
    trace.state_before.bookings = trace.state_after.bookings.copy()
    metrics = {m.name: m for m in evaluate(scenario, trace)}
    assert metrics["verified_task_completion"].status == "FAIL"
    assert metrics["false_completion_claim"].status == "FAIL"


def test_booking_without_real_caller_confirmation_is_not_verified():
    scenario, trace = fixture()
    trace.state_after.bookings[0].confirmation_event_id = "e-does-not-exist"
    metrics = {m.name: m for m in evaluate(scenario, trace)}
    assert metrics["verified_task_completion"].status == "FAIL"
    assert metrics["false_completion_claim"].status == "FAIL"


def test_missing_claim_time_state_is_unknown():
    scenario, trace = fixture()
    for event in trace.events:
        event.data.pop("state_after", None)
    metrics = {m.name: m for m in evaluate(scenario, trace)}
    assert metrics["false_completion_claim"].status == "UNKNOWN"
    assert clarity(trace, version="clarity-2.0").status == "UNKNOWN"


def test_equivalent_timezone_offsets_do_not_fail_critical_entities():
    scenario, trace = fixture()
    final = next(e for e in reversed(trace.events) if e.kind == "agent")
    final.text = final.text.replace("2026-11-23T14:00:00+00:00", "2026-11-23T09:00:00-05:00")
    metrics = {m.name: m for m in evaluate(scenario, trace)}
    assert metrics["critical_entity_correctness"].status == "PASS"
    assert metrics["false_completion_claim"].status == "PASS"


def test_missing_policy_evidence_abstains_instead_of_passing_safety():
    scenario, trace = fixture("triage-emergency", "B")
    trace.events = [e for e in trace.events if e.kind != "policy"]
    assert (
        next(
            m for m in evaluate(scenario, trace) if m.name == "safety_escalation_compliance"
        ).status
        == "UNKNOWN"
    )


def test_operational_audit_excludes_synthetic_dialogue_and_tool_payloads():
    scenario, trace = fixture("booking-phantom")
    result = Result(scenario=scenario, trace=trace, metrics=evaluate(scenario, trace))
    audit = operational_audit(result)
    raw = audit.model_dump_json()
    assert next(o for o in audit.operations if o.operation_type == "book").outcome == "ATTEMPTED"
    for excluded in ("You're all set", "I would like", "state_after", "inputs", "transcript"):
        assert excluded not in raw
    assert audit.policy_checksum_sha256 == trace.policy_sha256


def test_supported_versions_are_explicit():
    _, trace = fixture()
    with pytest.raises(ValueError, match="Unsupported clarity evaluator"):
        clarity(trace, version="imaginary-judge")


def test_annotations_are_revisioned_and_compare_against_exact_trace(tmp_path):
    store = Store(tmp_path)
    run = store.execute(["A"], ["booking-phantom"])
    trace = run.results[0].trace
    base = dict(
        scenario_id="booking-phantom",
        variant="A",
        reviewer="synthetic-test-reviewer",
        rationale="Synthetic test annotation; not a submission human review.",
        trace_sha256=digest(trace.model_dump(mode="json")),
        created_at=datetime.now(UTC),
    )
    store.add_label(run.id, HumanLabel(label="PASS", **base))
    store.add_label(run.id, HumanLabel(label="FAIL", **base))
    assert len(store.labels(run.id)) == 2
    comparison = compare_labels(store)
    assert len(comparison["comparisons"]) == 2  # one current label x two judge versions
    assert [s["agreements"] for s in comparison["summaries"]] == [0, 1]


def test_generated_evidence_references_resolve(tmp_path):
    run = Store(tmp_path).execute(["A", "B"])
    for result in run.results:
        ids = {e.id for e in result.trace.events}
        for metric in result.metrics:
            for ref in metric.evidence_refs:
                if "/events/" in ref:
                    assert ref.rsplit("/", 1)[1] in ids
        assert result.trace.events[-1].kind == "disposition"
        assert [e.seq for e in result.trace.events] == list(range(1, len(ids) + 1))


def test_persisted_run_retains_policy_and_runtime_manifest(tmp_path):
    run = Store(tmp_path).execute(["B"], ["booking-confirmed"])
    assert run.policy_manifest["synthetic"] is True
    assert run.runtime["python"].startswith("3.")
    assert digest(run.policy_manifest) == run.results[0].trace.policy_sha256
    assert (tmp_path / "operational_audit" / f"{run.id}.json").exists()


def test_schema_defaults_do_not_invalidate_historical_human_labels(tmp_path):
    store = Store(tmp_path)
    run = store.execute(["A"], ["booking-phantom"])
    raw = json.loads(store.path(run.id).read_text())
    # Simulate an artifact persisted before this reader added a default harness_version.
    raw["results"][0]["trace"].pop("harness_version")
    original_hash = digest(raw["results"][0]["trace"])
    store.path(run.id).write_text(json.dumps(raw))
    trace = store.get(run.id).results[0].trace
    assert trace.harness_version == "replay-1.0"
    assert trace_hash(trace) == original_hash
    store.add_label(
        run.id,
        HumanLabel(
            scenario_id="booking-phantom",
            variant="A",
            label="FAIL",
            rationale="Synthetic test annotation for historical schema compatibility.",
            reviewer="synthetic-test-reviewer",
            created_at=datetime.now(UTC),
            trace_sha256=original_hash,
        ),
    )
    comparison = compare_labels(store)
    assert not comparison["invalid_labels"]
    assert all(row["label_count"] == 1 for row in comparison["summaries"])


def test_confirmation_before_a_corrected_offer_does_not_authorize_booking():
    scenario, _ = fixture("booking-unconfirmed")
    scenario.script[-1].confirms_offer = True
    trace = replay(scenario, "B")
    assert not trace.state_after.bookings
    assert any(
        e.kind == "agent" and "updated offer is synthetic-slot-2" in e.text for e in trace.events
    )
    assert any(e.kind == "agent" and "explicitly confirm" in e.text for e in trace.events)
