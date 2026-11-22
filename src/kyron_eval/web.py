"""Loopback-only workbench. All displayed outcomes come from persisted run artifacts."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from kyron_eval.calibration import compare_labels
from kyron_eval.models import HumanLabel, Model
from kyron_eval.replay import digest, load_scenarios, trace_hash
from kyron_eval.storage import Store

ROOT = Path(__file__).parent


class RunRequest(Model):
    variants: list[Literal["A", "B"]] = Field(default=["A", "B"], min_length=1, max_length=2)
    scenario_ids: list[str] | None = Field(default=None, min_length=1, max_length=8)


class LabelRequest(Model):
    scenario_id: str
    variant: Literal["A", "B"]
    label: Literal["PASS", "FAIL", "UNKNOWN"]
    rationale: str = Field(min_length=5, max_length=4000)
    reviewer: str = Field(min_length=1, max_length=100)
    trace_sha256: str


def counts(results, variant):
    names = list(dict.fromkeys(m.name for r in results for m in r.metrics))
    return [
        {
            "name": name,
            **{
                status: sum(
                    m.status == status
                    for r in results
                    if r.trace.variant == variant
                    for m in r.metrics
                    if m.name == name
                )
                for status in ("PASS", "FAIL", "UNKNOWN", "N/A", "ERROR")
            },
        }
        for name in names
    ]


def create_app(artifact_root=None):
    app = FastAPI(title="Kyron Eval · synthetic offline workbench", version="0.1.0")
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
    )
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")
    templates.env.globals.update(digest=digest, trace_hash=trace_hash, counts=counts)
    store = Store(artifact_root)
    app.state.store = store

    def get_run(run_id):
        try:
            return store.get(run_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(404, "Run not found or invalid artifact") from exc

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "synthetic-offline"}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={**store.list(), "scenarios": load_scenarios()},
        )

    @app.get("/scenarios", response_class=HTMLResponse)
    def scenarios_page(request: Request):
        return templates.TemplateResponse(
            request=request, name="scenarios.html", context={"scenarios": load_scenarios()}
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str, scenario: str | None = None):
        run = get_run(run_id)
        scenario_ids = list(dict.fromkeys(r.scenario.id for r in run.results))
        selected = scenario or scenario_ids[0]
        pair = [r for r in run.results if r.scenario.id == selected]
        if not pair:
            raise HTTPException(404, "Scenario not present in run")
        labels = store.labels(run_id)
        return templates.TemplateResponse(
            request=request,
            name="run.html",
            context={
                "run": run,
                "pair": pair,
                "selected": selected,
                "labels": labels,
                "audit_available": (store.root / "operational_audit" / f"{run.id}.json").exists(),
                "scenario_rows": [
                    next(r for r in run.results if r.scenario.id == s) for s in scenario_ids
                ],
            },
        )

    @app.get("/api/scenarios")
    def api_scenarios():
        return load_scenarios()

    @app.get("/calibration", response_class=HTMLResponse)
    def calibration_page(request: Request):
        return templates.TemplateResponse(
            request=request, name="calibration.html", context={"comparison": compare_labels(store)}
        )

    @app.get("/api/calibration")
    def calibration_api():
        return compare_labels(store)

    @app.get("/findings", response_class=HTMLResponse)
    def findings_page(request: Request):
        experiment = None
        for entry in store.list()["runs"]:
            candidate = store.get(entry["id"])
            if set(candidate.variants) == {"A", "B"} and len(candidate.results) == 16:
                experiment = candidate
                break
        totals = []
        if experiment:
            for item in counts(experiment.results, "A"):
                name = item["name"]
                b = next(c for c in counts(experiment.results, "B") if c["name"] == name)
                totals.append({"name": name, "A": item["FAIL"], "B": b["FAIL"]})
        return templates.TemplateResponse(
            request=request,
            name="findings.html",
            context={"experiment": experiment, "totals": totals},
        )

    @app.get("/api/runs")
    def api_runs():
        return store.list()

    @app.post("/api/runs", status_code=201)
    def execute(body: RunRequest):
        if len(body.variants) != len(set(body.variants)):
            raise HTTPException(422, "Duplicate variants")
        try:
            return store.execute(body.variants, body.scenario_ids)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: str):
        get_run(run_id)
        return json.loads(store.path(run_id).read_text())

    @app.get("/api/runs/{run_id}/labels")
    def api_labels(run_id: str):
        get_run(run_id)
        return store.labels(run_id)

    @app.get("/api/runs/{run_id}/audit")
    def api_audit(run_id: str):
        get_run(run_id)
        path = store.root / "operational_audit" / f"{run_id}.json"
        if not path.exists():
            raise HTTPException(404, "This historical run predates the operational audit export")
        return json.loads(path.read_text())

    @app.post("/api/runs/{run_id}/labels", status_code=201)
    def save_label(run_id: str, body: LabelRequest):
        get_run(run_id)
        label = HumanLabel(**body.model_dump(), created_at=datetime.now(UTC))
        try:
            store.add_label(run_id, label)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return label

    return app


app = create_app()
