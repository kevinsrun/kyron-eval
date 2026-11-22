"""Loopback-only workbench. All displayed outcomes come from persisted run artifacts."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from kyron_eval.calibration import compare_labels
from kyron_eval.ehr.config import configuration_status, create_adapter
from kyron_eval.ehr.contracts import OPERATIONS, Fault
from kyron_eval.llm_agent import model_status
from kyron_eval.models import HumanLabel, Model
from kyron_eval.replay import digest, load_scenarios, trace_hash
from kyron_eval.storage import Store, atomic_json

ROOT = Path(__file__).parent


class RunRequest(Model):
    variants: list[Literal["A", "B", "LLM", "VOICE"]] = Field(
        default=["A", "B"], min_length=1, max_length=4
    )
    scenario_ids: list[str] | None = Field(default=None, min_length=1, max_length=9)
    adapter: Literal["mock", "fhir", "openemr"] = "mock"
    ehr_faults: dict[str, Fault] = Field(default_factory=dict, max_length=9)


class LabelRequest(Model):
    scenario_id: str
    variant: Literal["A", "B", "LLM", "VOICE"]
    label: Literal["PASS", "FAIL", "UNKNOWN"]
    rationale: str = Field(min_length=5, max_length=4000)
    reviewer: str = Field(min_length=1, max_length=100)
    trace_sha256: str


def voice_status():
    from importlib.metadata import PackageNotFoundError, version

    try:
        sdk_version = version("guava-sdk")
    except PackageNotFoundError:
        sdk_version = None
    credentials = bool(os.environ.get("GUAVA_API_KEY") and os.environ.get("GUAVA_AGENT_NUMBER"))
    return {
        "sdk_installed": sdk_version is not None,
        "sdk_version": sdk_version,
        "live_telephony_configured": False,
        "credentials_present": credentials,
        "synthetic_voice_ready": sdk_version == "0.53.0",
        "mode": "GUAVA_SDK_CALLBACK_SIMULATION",
        "live_credentials_status": "not implemented; no telephony or model execution",
        "actual_audio": False,
        "actual_telephony": False,
        "actual_model": False,
    }


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
    app.state.templates = templates
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
        catalog = load_scenarios()
        integration = configuration_status()
        if integration["openemr_configured"]:
            from kyron_eval.openemr_scenarios import cancellation_scenario

            catalog.append(cancellation_scenario())
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                **store.list(),
                "scenarios": catalog,
                "integration": integration,
                "model": model_status(),
                "voice": voice_status(),
            },
        )

    @app.get("/api/voice/status")
    def api_voice_status():
        return voice_status()

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

    @app.get("/openemr", response_class=HTMLResponse)
    def openemr_evidence_page(request: Request):
        paths = sorted((store.root / "openemr").glob("experiment-*.json"), reverse=True)
        artifact = json.loads(paths[0].read_text()) if paths else None
        return templates.TemplateResponse(
            request=request,
            name="openemr.html",
            context={"experiment": artifact, "artifact_name": paths[0].stem if paths else None},
        )

    @app.get("/api/openemr/experiments/{name}")
    def openemr_experiment(name: str):
        import re

        if not re.fullmatch(r"experiment-[A-Za-z0-9-]+", name):
            raise HTTPException(404, "Experiment not found")
        path = store.root / "openemr" / f"{name}.json"
        if not path.exists():
            raise HTTPException(404, "Experiment not found")
        return json.loads(path.read_text())

    @app.get("/api/ehr/status")
    def ehr_status():
        scenario = load_scenarios()[0]
        provider = create_adapter("mock", scenario)
        try:
            return {
                **configuration_status(),
                "mock": provider.capabilities(),
                "model": model_status(),
            }
        finally:
            provider.close()

    @app.post("/api/ehr/probe")
    def ehr_probe(adapter: Literal["fhir", "openemr"] = "fhir"):
        scenario = load_scenarios()[0]
        try:
            provider = create_adapter(adapter, scenario)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            patient_id = scenario.initial_state.patient_id
            results = {
                "capabilities": provider.capabilities(),
                "patient": provider.get_patient(patient_id),
                "appointments": provider.get_appointments(patient_id),
                "availability": provider.get_availability(scenario.clock),
                "clinical_context": provider.get_clinical_context(patient_id),
            }
            if adapter == "openemr":
                results["patient_search"] = provider.search_patients(patient_id)
                results["synthetic_patients"] = [
                    provider.get_patient(alias) for alias in provider.patient_map
                ]
                results["authorization"] = provider.authorization
                results["http_evidence"] = provider.http_events
            probe_id = (
                "probe-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:10]
            )
            results["probe_id"] = probe_id
            results["adapter"] = adapter
            atomic_json(store.root / "ehr_probes" / f"{probe_id}.json", jsonable_encoder(results))
            results["artifact_url"] = f"/api/ehr/probes/{probe_id}"
            return results
        finally:
            provider.close()

    @app.get("/api/ehr/probes/{probe_id}")
    def saved_probe(probe_id: str):
        import re

        if not re.fullmatch(r"probe-[A-Za-z0-9-]+", probe_id):
            raise HTTPException(404, "Probe not found")
        path = store.root / "ehr_probes" / f"{probe_id}.json"
        if not path.exists():
            raise HTTPException(404, "Probe not found")
        return json.loads(path.read_text())

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
        if set(body.ehr_faults) - set(OPERATIONS):
            raise HTTPException(422, "Unknown EHR fault operation")
        try:
            return store.execute(
                body.variants,
                body.scenario_ids,
                adapter_name=body.adapter,
                ehr_faults=body.ehr_faults,
            )
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

    from kyron_eval.triage.web import register_benchmarks

    register_benchmarks(app, templates, store.root)
    return app


app = create_app()
