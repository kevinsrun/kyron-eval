"""Local-only ingestion + predeclared constant baseline. Does not call any model/API."""

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from kyron_eval.storage import atomic_json
from kyron_eval.triage.datasets import CARE_LABELS, care_records, local_root, sen_records
from kyron_eval.triage.evaluation import TriageEvaluationService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--care-source", type=Path, required=True)
    parser.add_argument("--sen-source", type=Path)
    args = parser.parse_args()
    root = local_root()
    root.mkdir(parents=True, exist_ok=True)
    protocol_path = Path("artifacts/triage-validation/protocol.json")
    protocol = json.loads(protocol_path.read_text())  # Frozen BEFORE reading the reference file.
    revision = subprocess.check_output(
        ["git", "-C", str(args.care_source), "rev-parse", "HEAD"], text=True
    ).strip()
    source = args.care_source / "data/public/evaluation_prefixes_public.jsonl"
    records = care_records(source, revision)
    # Original and normalized local data are ignored. They are never sent to external services.
    (root / "care-original.jsonl").write_bytes(source.read_bytes())
    (root / "care.jsonl").write_text("\n".join(r.model_dump_json() for r in records) + "\n")
    metadata = {
        "imported_at": datetime.now(UTC).isoformat(),
        "revision": revision,
        "records": len(records),
        "cases": len({r.case_group_id for r in records}),
        "splits": dict(Counter(r.source_split for r in records)),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "license": "CC BY-NC 4.0 dataset; underlying source terms apply",
        "source_url": "https://github.com/ningkko/CARE-bench",
        "labels": "Human-reviewed, AI-assisted; not independent physician adjudication",
        "raw_context": "Local ignored data only; no external inference or redistribution",
        "clinical_validation": False,
    }
    if args.sen_source:
        sen_revision = subprocess.check_output(
            ["git", "-C", str(args.sen_source), "rev-parse", "HEAD"], text=True
        ).strip()
        sen = sen_records(args.sen_source / "dataset.csv", sen_revision)
        (root / "sen.jsonl").write_text("\n".join(r.model_dump_json() for r in sen) + "\n")
        metadata["sen2med"] = {
            "revision": sen_revision,
            "source_rows": len(sen) // 3,
            "normalized_task_records": len(sen),
            "evaluated": 0,
            "status": "NOT RUN",
            "split": "unspecified; no fabricated train/test partition",
            "context": "Withheld; includes real patient-derived source posts",
            "licensing_blocker": "README CC BY-SA 4.0 conflicts with root MIT LICENSE",
            "raw_data": "Original CSV retained only in local source clone; not redistributed",
            "clinician_disagreement_records": sum(r.uncertainty["disagreement"] for r in sen),
        }
    atomic_json(root / "manifest.json", metadata)
    chosen = [r for r in records if r.source_split == protocol["evaluated_split"]]
    predictions = {
        r.source_prefix_id: {
            "action": CARE_LABELS[0],
            "raw_output": CARE_LABELS[0],
            "model": "constant-info-needed-v1",
            "confidence": None,
            "execution": "LOCAL_CONSTANT_BASELINE",
        }
        for r in chosen
    }
    score = TriageEvaluationService().score(chosen, predictions)
    score.update(
        dataset="CARE-Bench",
        dataset_version="1.1@" + revision,
        protocol=protocol,
        dataset_manifest=metadata,
        created_at=datetime.now(UTC).isoformat(),
        label_provenance=metadata["labels"],
        held_out_evaluation="NOT RUN",
    )
    output = Path("artifacts/triage-validation") / (
        "benchmark-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6] + ".json"
    )
    atomic_json(output, score)
    print(
        json.dumps(
            {
                "artifact": str(output),
                "loaded": len(records),
                "evaluated": len(chosen),
                "cases": score["case_groups"],
                "correct": score["correct"],
                "incorrect": score["incorrect"],
                "unknown": score["unknown"],
            }
        )
    )


if __name__ == "__main__":
    main()
