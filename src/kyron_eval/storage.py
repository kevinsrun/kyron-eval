"""Atomic local JSON artifacts; runs are immutable and labels are separate revisions."""

import json
import os
import platform
import re
import tempfile
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from kyron_eval.audit import operational_audit
from kyron_eval.evaluate import CLARITY_VERSION, EVALUATOR_VERSION, evaluate
from kyron_eval.models import Metric, Result, Run, Status
from kyron_eval.replay import DATA, digest, load_scenarios, replay, trace_hash

RUN_ID = re.compile(r"^run-[a-zA-Z0-9-]+$")


def implementation_digest():
    root = Path(__file__).parent
    sources = {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*.py"))}
    vendor = root.parent / "clinical_triage"
    sources.update(
        {
            "clinical_triage/" + str(p.relative_to(vendor)): p.read_text()
            for p in sorted(vendor.rglob("*.py"))
        }
    )
    return digest(sources)


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, default=str)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or os.environ.get("KYRON_ARTIFACT_DIR", "artifacts"))

    def path(self, run_id):
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("Invalid run ID")
        return self.root / "runs" / f"{run_id}.json"

    def get(self, run_id) -> Run:
        return Run.model_validate_json(self.path(run_id).read_text())

    def list(self):
        rows, errors = [], []
        for path in sorted((self.root / "runs").glob("run-*.json"), reverse=True):
            try:
                run = Run.model_validate_json(path.read_text())
                rows.append(
                    {
                        "id": run.id,
                        "created_at": run.created_at.isoformat(),
                        "variants": run.variants,
                        "result_count": len(run.results),
                        "evaluator_version": run.evaluator_version,
                        "dataset_sha256": run.dataset_sha256,
                    }
                )
            except (ValueError, OSError) as exc:
                errors.append({"file": path.name, "error": str(exc)})
        return {"runs": rows, "artifact_errors": errors}

    def execute(self, variants, scenario_ids=None, *, clarity_version=CLARITY_VERSION):
        if not variants or len(set(variants)) != len(variants) or set(variants) - {"A", "B"}:
            raise ValueError("Choose A, B, or both exactly once")
        scenarios = load_scenarios()
        known = {s.id for s in scenarios}
        if scenario_ids and set(scenario_ids) - known:
            raise ValueError("Unknown scenario IDs")
        selected = [s for s in scenarios if not scenario_ids or s.id in scenario_ids]
        results = []
        for scenario in selected:
            for variant in variants:
                trace = replay(scenario, variant)
                try:
                    metrics = evaluate(scenario, trace, clarity_version=clarity_version)
                except Exception as exc:
                    # A judge implementation failure must never look like an agent failure.
                    metrics = [
                        Metric(
                            name="evaluation_error",
                            status=Status.ERROR,
                            reason=f"{type(exc).__name__}: {exc}",
                            evidence_refs=[],
                            evaluator_version=EVALUATOR_VERSION,
                        )
                    ]
                results.append(Result(scenario=scenario, trace=trace, metrics=metrics))
        run = Run(
            id=f"run-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:10]}",
            created_at=datetime.now(UTC),
            dataset_sha256=digest(json.loads((DATA / "scenarios.json").read_text())),
            implementation_sha256=implementation_digest(),
            runtime={
                "python": platform.python_version(),
                **{name: version(name) for name in ("fastapi", "pydantic", "jinja2", "uvicorn")},
            },
            policy_manifest=json.loads((DATA / "policies.json").read_text()),
            evaluator_version=f"{EVALUATOR_VERSION}+{clarity_version}",
            variants=variants,
            results=results,
        )
        atomic_json(
            self.root / "operational_audit" / f"{run.id}.json",
            {
                "evidence_class": "SANITIZED_OPERATIONAL_AUDIT",
                "synthetic": True,
                "records": [operational_audit(r).model_dump(mode="json") for r in results],
            },
        )
        atomic_json(self.path(run.id), run.model_dump(mode="json"))
        return run

    def add_label(self, run_id, label):
        run = self.get(run_id)
        result = next(
            (
                r
                for r in run.results
                if r.scenario.id == label.scenario_id and r.trace.variant == label.variant
            ),
            None,
        )
        if result is None:
            raise ValueError("Scenario / variant not found in this run")
        if trace_hash(result.trace) != label.trace_sha256:
            raise ValueError("Label does not match this exact trace")
        filename = f"{datetime.now(UTC):%Y%m%dT%H%M%S%f}-{uuid4().hex[:8]}.json"
        atomic_json(self.root / "labels" / run_id / filename, label.model_dump(mode="json"))

    def labels(self, run_id):
        self.path(run_id)  # validate before touching a path
        return [
            json.loads(p.read_text())
            for p in sorted((self.root / "labels" / run_id).glob("*.json"))
        ]
