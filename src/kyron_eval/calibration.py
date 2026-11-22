"""Keep raw judgments and human disagreement tied to exact immutable trace content."""

from datetime import UTC, datetime

from kyron_eval.evaluate import CLARITY_VERSIONS, clarity
from kyron_eval.replay import trace_hash
from kyron_eval.storage import atomic_json


def compare_labels(store, versions=CLARITY_VERSIONS):
    rows = []
    invalid_labels = []
    for entry in store.list()["runs"]:
        run = store.get(entry["id"])
        # Keep annotation history on disk; compare latest label per reviewer and exact trace.
        latest = {}
        for label in store.labels(run.id):
            latest[(label["trace_sha256"], label["reviewer"])] = label
        for label in latest.values():
            result = next(
                (
                    r
                    for r in run.results
                    if r.scenario.id == label["scenario_id"] and r.trace.variant == label["variant"]
                ),
                None,
            )
            if result is None or trace_hash(result.trace) != label["trace_sha256"]:
                invalid_labels.append(
                    {
                        "run_id": run.id,
                        "label": label,
                        "reason": "Trace not found or persisted trace hash mismatch",
                    }
                )
                continue
            for version in versions:
                judgment = clarity(result.trace, version=version)
                rows.append(
                    {
                        "run_id": run.id,
                        "scenario_id": result.scenario.id,
                        "variant": result.trace.variant,
                        "trace_sha256": label["trace_sha256"],
                        "human": label,
                        "automated": judgment.model_dump(mode="json"),
                        "agrees": label["label"] == judgment.status,
                    }
                )
    summaries = []
    for version in versions:
        subset = [r for r in rows if r["automated"]["evaluator_version"] == version]
        matrix = {
            h: {
                a: sum(r["human"]["label"] == h and r["automated"]["status"] == a for r in subset)
                for a in ("PASS", "FAIL", "UNKNOWN")
            }
            for h in ("PASS", "FAIL", "UNKNOWN")
        }
        summaries.append(
            {
                "version": version,
                "label_count": len(subset),
                "agreements": sum(r["agrees"] for r in subset),
                "disagreements": sum(not r["agrees"] for r in subset),
                "agreement_fraction": (
                    sum(r["agrees"] for r in subset) / len(subset) if subset else None
                ),
                "confusion_matrix": matrix,
            }
        )
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "synthetic": True,
        "warning": "Tiny convenience sample; calibration examples are not "
        "a held-out validation set.",
        "summaries": summaries,
        "comparisons": rows,
        "invalid_labels": invalid_labels,
    }


def save_comparison(store, versions=CLARITY_VERSIONS):
    comparison = compare_labels(store, versions)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    atomic_json(store.root / "calibration" / f"comparison-{stamp}.json", comparison)
    return comparison
