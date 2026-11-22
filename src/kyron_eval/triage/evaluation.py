"""Deterministic, taxonomy-specific reference scoring. Never confers clinical authority."""

import random
from collections import Counter, defaultdict

from kyron_eval.triage.datasets import CARE_LABELS


class TriageEvaluationService:
    version = "triage-reference-1.0"

    def compare(self, record, prediction):
        raw = prediction.get("action")
        reference = record.reference_action
        known = raw in record.label_taxonomy and reference in record.label_taxonomy
        urgent = record.label_taxonomy == list(CARE_LABELS)
        delta = (
            (CARE_LABELS.index(raw) - CARE_LABELS.index(reference)) if known and urgent else None
        )
        ratings = [v for v in record.annotations.values() if v is not None]
        return {
            "prefix_id": record.source_prefix_id,
            "case_group_id": record.case_group_id,
            "source_case_id": record.source_case_id,
            "split": record.source_split,
            "prediction": raw,
            "reference": reference,
            "status": ("PASS" if raw == reference else "FAIL") if known else "UNKNOWN",
            "undertriage": delta < 0 if delta is not None else None,
            "overtriage": delta > 0 if delta is not None else None,
            "urgent_miss": reference == CARE_LABELS[3] and raw != CARE_LABELS[3]
            if known and urgent
            else None,
            "clinician_reference_agreement": sum(v == raw for v in ratings) / len(ratings)
            if ratings and raw in record.label_taxonomy
            else None,
            "clinician_disagreement": len(set(ratings)) > 1 if ratings else None,
            "rater_missing_or_abstained": sum(v is None for v in record.annotations.values()),
            "raw_prediction": prediction,
            "raw_reference_label": record.raw_label,
            "evaluator_version": self.version,
            "evidence_refs": [record.source_prefix_id],
            "round_id": record.round_id,
        }

    def score(self, records, predictions):
        extra = set(predictions) - {r.source_prefix_id for r in records}
        if extra:
            raise ValueError("Prediction contains unknown prefix IDs")
        rows = [self.compare(r, predictions.get(r.source_prefix_id, {})) for r in records]
        taxonomy = records[0].label_taxonomy if records else []
        if any(r.label_taxonomy != taxonomy for r in records):
            raise ValueError("Do not combine different taxonomies")
        known = [r for r in rows if r["status"] != "UNKNOWN"]
        matrix = {label: {p: 0 for p in [*taxonomy, "UNKNOWN"]} for label in taxonomy}
        for r in rows:
            if r["reference"] in matrix:
                matrix[r["reference"]][
                    r["prediction"] if r["prediction"] in taxonomy else "UNKNOWN"
                ] += 1

        def ratio(a, b):
            return a / b if b else None

        classes = {}
        for label in taxonomy:
            tp = sum(r["reference"] == label and r["prediction"] == label for r in rows)
            classes[label] = {
                "precision": ratio(tp, sum(r["prediction"] == label for r in rows)),
                "recall": ratio(tp, sum(r["reference"] == label for r in rows)),
                "support": sum(r["reference"] == label for r in rows),
            }
        groups = defaultdict(list)
        for r in rows:
            groups[r["case_group_id"]].append(r["status"] == "PASS")
        rng = random.Random(916)
        samples, values = [], list(groups.values())
        if values:
            for _ in range(1000):
                sample = [x for g in rng.choices(values, k=len(values)) for x in g]
                samples.append(sum(sample) / len(sample))
            samples.sort()
        sequential = defaultdict(list)
        for r in rows:
            if r["round_id"] is not None:
                sequential[r["case_group_id"]].append(r)
        timing = Counter()
        for trajectory in sequential.values():
            trajectory.sort(key=lambda r: r["round_id"])
            gold = next(
                (r["round_id"] for r in trajectory if r["reference"] == CARE_LABELS[3]), None
            )
            pred = next(
                (r["round_id"] for r in trajectory if r["prediction"] == CARE_LABELS[3]), None
            )
            if gold is not None and (pred is None or pred > gold):
                timing["late_or_missing"] += 1
            if pred is not None and (gold is None or pred < gold):
                timing["early"] += 1
        return {
            "evaluator_version": self.version,
            "execution": "LOCAL_DETERMINISTIC_REFERENCE_COMPARISON",
            "evaluated_prefixes": len(rows),
            "case_groups": len(groups),
            "correct": sum(r["status"] == "PASS" for r in rows),
            "incorrect": sum(r["status"] == "FAIL" for r in rows),
            "unknown": sum(r["status"] == "UNKNOWN" for r in rows),
            "unknown_rate": ratio(sum(r["status"] == "UNKNOWN" for r in rows), len(rows)),
            "accuracy_all_prefixes": ratio(sum(r["status"] == "PASS" for r in rows), len(rows)),
            "accuracy_known_predictions": ratio(
                sum(r["status"] == "PASS" for r in known), len(known)
            ),
            "accuracy_95pct_case_bootstrap": [samples[24], samples[974]] if samples else None,
            "confusion_matrix": matrix,
            "per_class": classes,
            "urgent_sensitivity": classes.get(CARE_LABELS[3], {}).get("recall"),
            "undertriage_count": sum(r["undertriage"] is True for r in rows),
            "overtriage_count": sum(r["overtriage"] is True for r in rows),
            "urgent_misses": sum(r["urgent_miss"] is True for r in rows),
            "escalation_timing": dict(timing),
            "critical_question_coverage": {
                "status": "UNKNOWN",
                "reason": "No question-level annotations supplied",
            },
            "unsupported_reassurance": {
                "status": "UNKNOWN",
                "reason": "Direct labels do not establish spoken reassurance",
            },
            "calibration": {"status": "NOT AVAILABLE", "reason": "No probability predictions"},
            "clinician_disagreement_records": sum(
                r["clinician_disagreement"] is True for r in rows
            ),
            "clinical_validation": False,
            "rows": rows,
        }
