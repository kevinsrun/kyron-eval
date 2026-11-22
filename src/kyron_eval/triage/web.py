"""Read-only local benchmark inspection. No reference text enters voice/model requests."""

import json

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from kyron_eval.triage.datasets import load_records, local_root
from kyron_eval.triage.fixtures import DEMOS


def register_benchmarks(app, templates, artifact_root):
    @app.get("/api/triage/replays")
    def replays():
        paths = sorted((artifact_root / "triage-validation").glob("replay-*.json"))
        latest = {}
        for path in paths:
            row = json.loads(path.read_text())
            demo = row["encounter"]["demo_id"]
            if demo not in latest or row["latest_run"] > latest[demo]["latest_run"]:
                latest[demo] = row
        return [latest[key] for key in sorted(latest)]

    @app.get("/api/triage/demos")
    def demos():
        return {
            "source": "SYNTHETIC_ENGINEERING_EXPECTATIONS; not clinician labels",
            "external_reference_context": False,
            "demos": DEMOS,
        }

    def catalog():
        manifest = local_root() / "manifest.json"
        paths = sorted((artifact_root / "triage-validation").glob("benchmark-*.json"))
        return {
            "manifest": json.loads(manifest.read_text()) if manifest.exists() else None,
            "runs": [
                {
                    "id": p.stem,
                    **{k: v for k, v in json.loads(p.read_text()).items() if k != "rows"},
                }
                for p in reversed(paths)
            ],
        }

    @app.get("/triage-benchmarks", response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse(
            request=request, name="triage_benchmarks.html", context=catalog()
        )

    @app.get("/api/triage/benchmarks")
    def status():
        return catalog()

    @app.get("/api/triage/benchmarks/{run_id}")
    def run(run_id: str):
        if not run_id.startswith("benchmark-") or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
            for c in run_id
        ):
            raise HTTPException(404, "Benchmark not found")
        path = artifact_root / "triage-validation" / (run_id + ".json")
        if not path.exists():
            raise HTTPException(404, "Benchmark not found")
        return json.loads(path.read_text())

    @app.get("/api/triage/cases")
    def cases(dataset: str = "CARE-Bench", split: str = "development", offset: int = 0):
        if dataset not in {"CARE-Bench", "Sen2Med"}:
            raise HTTPException(422, "Unknown dataset")
        rows = [r for r in load_records(dataset) if r.source_split == split]
        offset = max(0, offset)
        return {
            "total": len(rows),
            "records": rows[offset : offset + 25],
            "restriction": "LOCAL ONLY; never submit this context to the voice API",
        }
