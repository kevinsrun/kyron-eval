"""Versioned LOCAL-ONLY dataset normalization. Never supplies external agent prompts."""

import csv
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field

CARE_LABELS = (
    "0A_NO_ESCALATION_INFO_NEEDED",
    "0B_NO_ESCALATION_SELF_CARE_MONITOR",
    "1A_ESCALATION_NONURGENT_CARE",
    "1B_ESCALATION_URGENT_CARE",
)
ACTION_NAMES = dict(
    zip(
        CARE_LABELS,
        ("Information needed", "Self-care / monitor", "Nonurgent care", "Urgent care"),
        strict=True,
    )
)


class ReferenceRecord(BaseModel):
    schema_version: str = "triage-record-1.0"
    dataset_id: str
    source_case_id: str
    source_prefix_id: str
    case_group_id: str
    dialogue_context: list[str]
    structured_symptoms: dict = Field(default_factory=dict)
    reference_action: str | None
    raw_label: str | None
    label_taxonomy: list[str]
    label_provenance: str
    rater_identity_category: str
    annotations: dict = Field(default_factory=dict)
    uncertainty: dict = Field(default_factory=dict)
    source_split: str
    dataset_version: str
    restrictions: str
    provenance: dict
    round_id: int | None = None
    leakage_keys: list[str] = Field(default_factory=list)


def validate_groups(records):
    """Reject duplicate prefixes and shared source/case/context across partitions."""
    seen, groups = set(), {}
    for row in records:
        key = (row.dataset_id, row.source_prefix_id)
        if key in seen:
            raise ValueError("Duplicate dataset prefix")
        seen.add(key)
        for group in [row.case_group_id, *row.leakage_keys]:
            key = (row.dataset_id, group)
            previous = groups.setdefault(key, row.source_split)
            if previous != row.source_split:
                raise ValueError(f"Grouped split leakage: {group}")


def care_records(path, revision):
    records = []
    for line in Path(path).read_text().splitlines():
        row = json.loads(line)
        label = row["gold_label"] or None
        if label is not None and label not in CARE_LABELS:
            raise ValueError("Unknown CARE label; refuse silent mapping")
        if row["split"] not in {"development", "validation", "public_test_1"}:
            raise ValueError("Unknown source split")
        record = ReferenceRecord(
            dataset_id="CARE-Bench",
            source_case_id=row["source_case_id"],
            source_prefix_id=row["prefix_id"],
            case_group_id=row["case_id"],
            dialogue_context=row["input_patient_turns"],
            reference_action=label,
            raw_label=label,
            label_taxonomy=list(CARE_LABELS),
            label_provenance="Human-reviewed, AI-assisted reference; not physician adjudication",
            rater_identity_category="human reviewer; individual ratings not released",
            uncertainty={
                "information_state": row["information_state"],
                "agreement": "not released",
                "urgent": label == CARE_LABELS[3],
            },
            source_split=row["split"],
            dataset_version=f"1.1@{revision}",
            restrictions="CC BY-NC 4.0 dataset; underlying source terms apply; LOCAL ONLY",
            provenance={
                "url": "https://github.com/ningkko/CARE-bench",
                "source_dataset": row["source_dataset"],
                "revision": revision,
            },
            round_id=row["round_id"],
            leakage_keys=[row["source_dataset"] + ":" + row["source_case_id"]],
        )
        records.append(record)
    validate_groups(records)
    return records


def sen_records(path, revision):
    """Preserve binary tasks, ratings and missingness; withhold patient-derived text.

    README says CC BY-SA 4.0 while LICENSE says MIT. No raw contexts redistributed,
    no external inference, no synthetic-origin claim. Original CSV stays local.
    """
    records = []
    with Path(path).open() as stream:
        for row in csv.DictReader(stream):
            for task in ("manage", "visit", "resource"):
                label = row[f"gold_{task}"] or None
                if label not in {None, "YES", "NO"}:
                    raise ValueError("Unknown Sen2Med label")
                ratings = {}
                for i in range(1, 6):
                    value = row[f"clin_rater{i}_{task}"]
                    if value not in {"", "0.0", "1.0"}:
                        raise ValueError("Unknown clinician annotation")
                    ratings[f"clin_rater{i}"] = {"": None, "0.0": "NO", "1.0": "YES"}[value]
                known = [v for v in ratings.values() if v is not None]
                records.append(
                    ReferenceRecord(
                        dataset_id=f"Sen2Med/{task}",
                        source_case_id=row["context_id"],
                        source_prefix_id=f"{row['context_id']}:{row['perturbation']}:{task}",
                        case_group_id=row["context_id"],
                        dialogue_context=[],
                        reference_action=label,
                        raw_label=label,
                        label_taxonomy=["NO", "YES"],
                        label_provenance=(
                            "Released gold label plus separate clinician-rater annotations"
                        ),
                        rater_identity_category="clinician raters 1–5; pseudonymous categories",
                        annotations=ratings,
                        uncertainty={
                            "disagreement": len(set(known)) > 1,
                            "missing_or_abstained": 5 - len(known),
                            "abstention_reason": "not distinguishable from unannotated",
                        },
                        source_split="unspecified",
                        dataset_version=revision,
                        restrictions=(
                            "LOCAL ONLY; README CC BY-SA 4.0 / LICENSE MIT conflict unresolved"
                        ),
                        provenance={
                            "url": "https://github.com/abinithago/Sen2Med",
                            "revision": revision,
                            "source_dataset": row["dataset"],
                            "perturbation": row["perturbation"],
                            "context_withheld": True,
                            "context_sha256": hashlib.sha256(
                                row["clinical_context"].encode()
                            ).hexdigest(),
                        },
                    )
                )
    validate_groups(records)
    return records


def local_root():
    import os

    return Path(os.environ.get("KYRON_TRIAGE_DATA_DIR", ".datasets/triage"))


def load_records(dataset="CARE-Bench"):
    path = local_root() / ("care.jsonl" if dataset == "CARE-Bench" else "sen.jsonl")
    if not path.exists():
        return []
    return [ReferenceRecord.model_validate_json(line) for line in path.read_text().splitlines()]
