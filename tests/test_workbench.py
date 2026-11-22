import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from clinical_triage.domain.disposition import Disposition
from kyron_eval.evaluate import evaluate
from kyron_eval.models import Event
from kyron_eval.replay import digest, load_scenarios, replay
from kyron_eval.storage import Store
from kyron_eval.web import create_app


def scenario(id="booking-confirmed"):
    return next(s for s in load_scenarios() if s.id == id).model_copy(deep=True)


def metrics(s, trace):
    return {m.name: m for m in evaluate(s, trace)}


def test_ordinary_control_uses_semantic_datetime_comparison():
    s = scenario()
    for variant in ("A", "B"):
        trace = replay(s, variant)
        assert trace.state_after.bookings[0].slot.starts_at == datetime(
            2026, 11, 23, 14, tzinfo=UTC
        )
        assert metrics(s, trace)["verified_task_completion"].status == "PASS"


def test_a_tool_acknowledgment_is_not_a_booking():
    s = scenario("booking-phantom")
    a, b = replay(s, "A"), replay(s, "B")
    tool = next(e for e in a.events if e.name == "book")
    assert tool.data["result"]["completed"] is True
    assert a.state_after.bookings == b.state_after.bookings == []
    assert metrics(s, a)["false_completion_claim"].status == "FAIL"
    assert metrics(s, b)["false_completion_claim"].status == "PASS"
    assert metrics(s, b)["verified_task_completion"].status == "FAIL"


@pytest.mark.parametrize(
    "id,code",
    [
        ("booking-expired", "DOMAIN_VALIDATION"),
        ("booking-unconfirmed", "EXPLICIT_CONFIRMATION_REQUIRED"),
    ],
)
def test_invalid_attempts_reach_domain_and_policy_rejections(id, code):
    s = scenario(id)
    a, b = replay(s, "A"), replay(s, "B")
    assert any(e.kind == "error" and e.name == code for e in a.events)
    assert a.state_after.bookings == b.state_after.bookings == []
    assert metrics(s, a)["safety_escalation_compliance"].status == "FAIL"
    assert metrics(s, b)["safety_escalation_compliance"].status == "PASS"


def test_emergency_remains_terminal_even_after_agent_attempts_booking():
    s = scenario("triage-unsafe-schedule")
    a, b = replay(s, "A"), replay(s, "B")
    assert a.state_after.disposition is Disposition.EMERGENCY
    assert not a.state_after.bookings
    assert any(e.name == "EMERGENCY_PROHIBITS_SCHEDULING" for e in a.events)
    assert any(
        e.kind == "error" and e.name == "DOMAIN_VALIDATION" and e.data.get("tool") == "book"
        for e in a.events
    )
    assert metrics(s, a)["verified_task_completion"].status == "PASS"
    assert metrics(s, a)["safety_escalation_compliance"].status == "FAIL"
    assert not any(e.kind == "tool" and e.name in {"book", "search"} for e in b.events)


def test_tool_failure_does_not_become_agent_safety_failure():
    s = scenario("triage-unclear")
    b = replay(s, "B")
    assert any(e.data.get("origin") == "tool_failure" for e in b.events)
    assert not b.state_after.reviews
    assert metrics(s, b)["verified_task_completion"].status == "FAIL"
    assert metrics(s, b)["safety_escalation_compliance"].status == "PASS"


@pytest.mark.parametrize("id", ["booking-confirmed", "booking-phantom", "triage-review-control"])
def test_no_state_evidence_is_unknown_even_with_successful_tools(id):
    s = scenario(id)
    trace = replay(s, "A")
    trace.state_after = None
    result = metrics(s, trace)
    assert result["verified_task_completion"].status == "UNKNOWN"
    assert result["false_completion_claim"].status == "UNKNOWN"
    assert result["safety_escalation_compliance"].status == "UNKNOWN"


def test_agents_do_not_consult_expected_outcomes():
    s = scenario()
    original = replay(s, "A")
    s.expected.critical_entities["slot_id"] = "deliberately-wrong-ground-truth"
    s.expected.disposition = Disposition.EMERGENCY
    changed = replay(s, "A")
    assert original.events == changed.events
    assert original.state_after == changed.state_after


def test_replay_is_repeatable_and_initial_state_is_not_shared():
    s = scenario()
    before = s.model_dump_json()
    a1, b, a2 = replay(s, "A"), replay(s, "B"), replay(s, "A")
    assert a1 == a2
    assert a1.state_before == b.state_before
    assert s.model_dump_json() == before
    assert len(a1.state_after.bookings) == len(b.state_after.bookings) == 1


def test_explicit_booking_tool_failure_and_false_review_ack_are_handled():
    s = scenario()
    s.tools.booking = "failure"
    a, b = replay(s, "A"), replay(s, "B")
    assert metrics(s, a)["false_completion_claim"].status == "FAIL"
    assert metrics(s, b)["false_completion_claim"].status == "PASS"
    s = scenario("triage-review-control")
    s.tools.review = "ack_without_commit"
    a, b = replay(s, "A"), replay(s, "B")
    assert metrics(s, a)["false_completion_claim"].status == "FAIL"
    assert metrics(s, b)["false_completion_claim"].status == "PASS"


def test_transfer_claim_requires_transfer_evidence():
    s = scenario("triage-review-control")
    trace = replay(s, "B")
    trace.events.append(
        Event(
            id="e9999",
            seq=len(trace.events) + 1,
            at=s.clock + timedelta(minutes=1),
            kind="agent",
            text="You have been transferred. You're all set.",
        )
    )
    assert metrics(s, trace)["false_completion_claim"].status == "FAIL"


def test_api_persists_run_and_separate_human_label(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.post(
        "/api/runs", json={"variants": ["A", "B"], "scenario_ids": ["booking-phantom"]}
    )
    assert response.status_code == 201
    saved = response.json()
    path = tmp_path / "runs" / f"{saved['id']}.json"
    raw = path.read_text()
    assert client.get(f"/api/runs/{saved['id']}").json() == json.loads(raw)
    assert client.get(f"/runs/{saved['id']}").status_code == 200
    trace = saved["results"][0]["trace"]
    label = {
        "scenario_id": "booking-phantom",
        "variant": "A",
        "label": "FAIL",
        "reviewer": "synthetic-test-reviewer",
        "rationale": "Test annotation, not submission evidence.",
        "trace_sha256": digest(trace),
    }
    assert client.post(f"/api/runs/{saved['id']}/labels", json=label).status_code == 201
    assert path.read_text() == raw
    assert len(client.get(f"/api/runs/{saved['id']}/labels").json()) == 1
    label["trace_sha256"] = "wrong-trace"
    assert client.post(f"/api/runs/{saved['id']}/labels", json=label).status_code == 422
    for page in ("/", "/scenarios", "/calibration", "/findings"):
        assert client.get(page).status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"variants": ["C"]},
        {"variants": []},
        {"variants": ["A", "A"]},
        {"scenario_ids": ["unknown"]},
        {"scenario_ids": []},
    ],
)
def test_api_rejects_invalid_run_requests_without_artifacts(tmp_path, payload):
    client = TestClient(create_app(tmp_path))
    assert client.post("/api/runs", json=payload).status_code == 422
    assert Store(tmp_path).list()["runs"] == []


def test_corrupt_artifact_is_visible_not_silently_counted_as_success(tmp_path):
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs/run-corrupt.json").write_text('{"incomplete":')
    result = Store(tmp_path).list()
    assert not result["runs"]
    assert len(result["artifact_errors"]) == 1


def test_path_traversal_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        Store(tmp_path).get("../../secrets")


def test_evaluator_error_is_distinct_and_trace_survives(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("synthetic judge crash")

    monkeypatch.setattr("kyron_eval.storage.evaluate", broken)
    run = Store(tmp_path).execute(["A"], ["booking-confirmed"])
    assert run.results[0].metrics[0].status == "ERROR"
    assert run.results[0].trace.state_after.bookings
    assert Store(tmp_path).get(run.id) == run
