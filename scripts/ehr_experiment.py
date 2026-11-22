"""Create new immutable EHR experiment artifacts; never overwrite the original report."""

import argparse
from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

from kyron_eval.llm_agent import model_status
from kyron_eval.storage import Store, atomic_json

PROFILES = {
    "control": {},
    "write_rejected": {"create_appointment": "failure"},
    "write_timeout_no_commit": {"create_appointment": "timeout"},
    "write_timeout_after_commit": {"create_appointment": "timeout_after_commit"},
    "ack_without_commit": {"create_appointment": "ack_without_commit"},
    "wrong_slot_committed": {"create_appointment": "mismatch"},
    "stale_availability": {"get_availability": "stale"},
    "readback_unavailable": {"read_state": "readback_failure"},
    "stale_appointments": {"get_appointments": "stale"},
    "identity_unavailable": {"verify_identity": "failure"},
}


def summarize(run):
    return {
        "run_id": run.id,
        "configuration": run.configuration,
        "implementation_sha256": run.implementation_sha256,
        "results": [
            {
                "scenario": result.scenario.id,
                "variant": result.trace.variant,
                "metrics": {metric.name: metric.status for metric in result.metrics},
                "final_message": [e.text for e in result.trace.events if e.kind == "agent"][-1],
                "readback_status": result.trace.ehr["final_readback"]["status"],
            }
            for result in run.results
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--include-fhir", action="store_true", help="Use configured synthetic endpoint"
    )
    args = parser.parse_args()
    store = Store()
    baseline = store.execute(["A", "B"])
    experiments = {"paired_suite": summarize(baseline)}
    for name, faults in PROFILES.items():
        experiments[name] = summarize(
            store.execute(["A", "B"], ["booking-confirmed"], ehr_faults=faults)
        )
    if args.include_fhir:
        experiments["readonly_fhir"] = summarize(
            store.execute(["B"], ["booking-confirmed"], adapter_name="fhir")
        )
    output = store.root / (
        "ehr-experiment-"
        + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        + "-"
        + uuid4().hex[:8]
        + ".json"
    )
    summary = {
        "created_at": datetime.now(UTC).isoformat(),
        "base_commit": "56b0a8e",
        "synthetic": True,
        "real_ehr_persistence_demonstrated": False,
        "actual_llm_execution": False,
        "llm_configuration": model_status(),
        "fhir_evidence_note": "Configured synthetic read-only endpoint; no writes.",
        "experiments": experiments,
        "paired_counts": {
            variant: {
                name: dict(
                    Counter(
                        m.status
                        for r in baseline.results
                        if r.trace.variant == variant
                        for m in r.metrics
                        if m.name == name
                    )
                )
                for name in dict.fromkeys(m.name for r in baseline.results for m in r.metrics)
            }
            for variant in ["A", "B"]
        },
    }
    atomic_json(output, summary)
    print(output)
    print("Paired suite: " + baseline.id)
    for variant, counts in summary["paired_counts"].items():
        print(variant, counts)
    for name, experiment in experiments.items():
        if name != "paired_suite":
            print(
                name,
                [
                    (
                        r["variant"],
                        r["metrics"]["ehr_write_verification"],
                        r["metrics"]["ehr_claim_support"],
                        r["metrics"]["resolution_clarity"],
                    )
                    for r in experiment["results"]
                ],
            )


if __name__ == "__main__":
    main()
