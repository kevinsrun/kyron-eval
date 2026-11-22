"""Synthetic schema/metric/policy regressions; these do not establish clinical accuracy."""

import json

import pytest
from fastapi.testclient import TestClient

from kyron_eval.triage.datasets import (
    CARE_LABELS,
    ReferenceRecord,
    care_records,
    sen_records,
    validate_groups,
)
from kyron_eval.triage.evaluation import TriageEvaluationService
from kyron_eval.triage.routing import TriageEncounter
from kyron_eval.web import create_app


def record(prefix="p1", group="g1", label=CARE_LABELS[0], split="development", round_id=1):
    return ReferenceRecord(
        dataset_id="synthetic-test",
        source_case_id=group,
        source_prefix_id=prefix,
        case_group_id=group,
        dialogue_context=["Fictional context"],
        reference_action=label,
        raw_label=label,
        label_taxonomy=list(CARE_LABELS),
        label_provenance="synthetic test",
        rater_identity_category="synthetic",
        source_split=split,
        dataset_version="test",
        restrictions="synthetic",
        provenance={},
        round_id=round_id,
    )


def test_group_leakage_duplicate_prefix_and_source_overlap_rejected():
    with pytest.raises(ValueError, match="leakage"):
        validate_groups([record(), record("p2", split="public_test_1")])
    with pytest.raises(ValueError, match="Duplicate"):
        validate_groups([record(), record()])
    a, b = record(), record("p2", "g2", split="validation")
    a.leakage_keys = b.leakage_keys = ["same-source-encounter"]
    with pytest.raises(ValueError, match="leakage"):
        validate_groups([a, b])


def test_care_schema_preserves_unknown_and_rejects_unmapped_label(tmp_path):
    raw = dict(
        prefix_id="p",
        case_id="g",
        round_id=2,
        split="validation",
        source_dataset="synthetic",
        source_case_id="s",
        gold_label=None,
        input_patient_turns=["Synthetic caller"],
        information_state="unclear",
    )
    path = tmp_path / "data.jsonl"
    path.write_text(json.dumps(raw))
    result = care_records(path, "test-revision")[0]
    assert result.reference_action is None
    assert result.round_id == 2 and result.source_split == "validation"
    assert "not physician" in result.label_provenance
    raw["gold_label"] = "NONURGENT"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="Unknown CARE"):
        care_records(path, "test")


def test_sen_preserves_binary_labels_disagreement_and_unrated(tmp_path):
    import csv

    row = {
        "context_id": "synthetic-context",
        "perturbation": "gender_swap",
        "dataset": "synthetic-test",
        "clinical_context": "Fictional context",
    }
    for task in ["manage", "visit", "resource"]:
        row[f"gold_{task}"] = "YES" if task == "visit" else ""
        for i in range(1, 6):
            row[f"clin_rater{i}_{task}"] = "1.0" if i == 1 else "0.0" if i == 2 else ""
    path = tmp_path / "sen.csv"
    with path.open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    records = sen_records(path, "test")
    assert len(records) == 3 and records[1].reference_action == "YES"
    assert records[0].reference_action is None
    assert records[0].annotations["clin_rater3"] is None
    assert records[0].uncertainty["disagreement"] is True
    assert records[0].dialogue_context == []
    assert all(r.label_taxonomy == ["NO", "YES"] for r in records)


def test_score_confusion_unknown_denominators_and_urgent_miss():
    rows = [
        record("a", "a", CARE_LABELS[3]),
        record("b", "b", CARE_LABELS[3]),
        record("c", "c", CARE_LABELS[2]),
        record("d", "d", None),
    ]
    predictions = {
        "a": {"action": CARE_LABELS[3]},
        "b": {"action": CARE_LABELS[0]},
        "c": {"action": "unmapped"},
        "d": {"action": CARE_LABELS[0]},
    }
    score = TriageEvaluationService().score(rows, predictions)
    assert (score["correct"], score["incorrect"], score["unknown"]) == (1, 1, 2)
    assert score["urgent_sensitivity"] == 0.5
    assert score["undertriage_count"] == 1 and score["urgent_misses"] == 1
    assert score["confusion_matrix"][CARE_LABELS[2]]["UNKNOWN"] == 1
    assert score["accuracy_all_prefixes"] == 0.25
    assert score["critical_question_coverage"]["status"] == "UNKNOWN"
    assert score["calibration"]["status"] == "NOT AVAILABLE"
    with pytest.raises(ValueError, match="unknown prefix"):
        TriageEvaluationService().score(rows, {"other": {"action": CARE_LABELS[0]}})


def test_clinician_comparison_has_separate_taxonomy_and_abstentions():
    row = record()
    row.label_taxonomy, row.raw_label, row.reference_action = ["NO", "YES"], "YES", "YES"
    row.annotations = {"r1": "YES", "r2": "NO", "r3": None}
    result = TriageEvaluationService().compare(row, {"action": "YES"})
    assert result["clinician_reference_agreement"] == 0.5
    assert result["clinician_disagreement"] is True
    assert result["rater_missing_or_abstained"] == 1
    assert result["undertriage"] is None
    assert result["urgent_miss"] is None


def test_sequential_escalation_timing():
    rows = [record("p1", label=CARE_LABELS[0]), record("p2", label=CARE_LABELS[3], round_id=2)]
    score = TriageEvaluationService().score(
        rows, {"p1": {"action": CARE_LABELS[0]}, "p2": {"action": CARE_LABELS[0]}}
    )
    assert score["escalation_timing"]["late_or_missing"] == 1
    score = TriageEvaluationService().score(
        rows, {"p1": {"action": CARE_LABELS[3]}, "p2": {"action": CARE_LABELS[3]}}
    )
    assert score["escalation_timing"]["early"] == 1


def test_sequential_policy_anchors_extraction_and_never_authorizes_writes():
    encounter = TriageEncounter()
    encounter.observe("u1", "I do not feel well")
    encounter.extract(
        symptoms="headache", onset="yesterday", action=CARE_LABELS[2], source_event_ids=["u1"]
    )
    assert encounter.state == "TRIAGE_INCOMPLETE"  # Guessed facts rejected.
    encounter.observe("u2", "mild itchy patch for two weeks")
    encounter.extract(
        symptoms="mild itchy patch",
        onset="two weeks",
        action=CARE_LABELS[2],
        source_event_ids=["u2"],
    )
    assert encounter.state == "NONURGENT"
    assert not encounter.snapshot()["scheduling_permitted"]
    encounter.observe("u3", "new chest pain")
    assert encounter.state == "URGENT_ESCALATION"
    encounter.extract(
        symptoms="mild itchy patch",
        onset="two weeks",
        action=CARE_LABELS[1],
        source_event_ids=["u2"],
    )
    assert encounter.state == "URGENT_ESCALATION"  # Cannot de-escalate the sticky guard.
    assert len(encounter.transitions) >= 5


@pytest.mark.parametrize("action", [None, "UNKNOWN", "YES", "1A"])
def test_unmapped_action_never_opens_workflow(action):
    e = TriageEncounter()
    e.observe("a", "mild patch for two weeks")
    e.extract(symptoms="mild patch", onset="two weeks", action=action, source_event_ids=["a"])
    assert e.state == "TRIAGE_INCOMPLETE"


def test_duplicate_and_revised_utterances_invalidate_facts_without_false_emergency():
    e = TriageEncounter()
    e.observe("a", "mild itch")
    count = len(e.transitions)
    e.observe("a", "mild itch")
    assert len(e.transitions) == count
    e.observe("a", "changed transcript")
    assert e.state == "TRIAGE_INCOMPLETE"
    assert e.turns["a"] == "changed transcript"
    e.observe("a", "chest pain")
    assert e.state == "URGENT_ESCALATION"
    e.observe("a", "mild itch")
    assert e.state == "URGENT_ESCALATION"  # Previously observed risk stays conservative.


def test_benchmark_routes_no_fabricated_scores_or_raw_external_context(tmp_path, monkeypatch):
    monkeypatch.setenv("KYRON_TRIAGE_DATA_DIR", str(tmp_path / "local-only"))
    client = TestClient(create_app(tmp_path))
    page = client.get("/triage-benchmarks")
    assert page.status_code == 200 and "NOT RUN" in page.text
    assert client.get("/api/triage/benchmarks").json()["runs"] == []
    assert client.get("/api/triage/cases?dataset=other").status_code == 422
    assert client.get("/api/triage/benchmarks/benchmark-..%2Fsecret").status_code == 404
    assert "SYNTHETIC" in client.get("/api/triage/demos").json()["source"]


def test_scripted_action_source_is_explicit_and_never_implies_sdk_inference():
    encounter = TriageEncounter()
    encounter.observe("scripted-1", "mild patch for two weeks")
    encounter.extract(
        symptoms="mild patch",
        onset="two weeks",
        action=CARE_LABELS[2],
        source_event_ids=["scripted-1"],
        source="SCRIPTED_REPLAY_NO_MODEL",
    )
    assert encounter.snapshot()["prediction"]["source"] == "SCRIPTED_REPLAY_NO_MODEL"
    assert not encounter.snapshot()["scheduling_permitted"]


def test_exact_script_reference_comparison_abstains_after_changed_speech():
    from kyron_eval.triage.fixtures import DEMOS, compare_demo

    encounter = TriageEncounter()
    encounter.observe("u1", DEMOS["nonurgent"]["script"][0])
    encounter.extract(
        symptoms="mild itchy patch",
        onset="two weeks",
        action=CARE_LABELS[2],
        source_event_ids=["u1"],
    )
    assert compare_demo("nonurgent", encounter)["status"] == "PASS"
    encounter.observe("u1", "a different synthetic concern for two weeks")
    encounter.extract(
        symptoms="synthetic concern",
        onset="two weeks",
        action=CARE_LABELS[2],
        source_event_ids=["u1"],
    )
    assert compare_demo("nonurgent", encounter)["status"] == "UNKNOWN"


def test_high_risk_identity_phase_cannot_bypass_urgent_guard():
    encounter = TriageEncounter()
    encounter.observe("symptoms", "mild patch for two weeks")
    encounter.extract(
        symptoms="mild patch",
        onset="two weeks",
        action=CARE_LABELS[2],
        source_event_ids=["symptoms"],
    )
    encounter.observe("identity", "1980, but now I have chest pain", invalidate_prediction=False)
    assert encounter.state == "URGENT_ESCALATION"
