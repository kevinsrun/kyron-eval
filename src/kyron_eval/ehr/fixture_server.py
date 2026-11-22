"""Optional local synthetic FHIR fixture server. This is NOT OpenEMR.

Run: uv run uvicorn kyron_eval.ehr.fixture_server:app --host 127.0.0.1 --port 8300
Only GET routes exist. No PHI or provider credentials; all records are synthetic.
"""

from copy import deepcopy

from fastapi import FastAPI, HTTPException

from kyron_eval.ehr.fhir import SYNTHETIC_TAG

PATIENT_ID = "synthetic-patient-1"


def resource(kind, id, **fields):
    return {"resourceType": kind, "id": id, "meta": {"tag": [deepcopy(SYNTHETIC_TAG)]}, **fields}


def capability_statement():
    kinds = {
        "Patient": ["read"],
        "Appointment": ["search-type"],
        "Slot": ["search-type", "read"],
        "Schedule": ["read"],
        "Condition": ["search-type"],
        "MedicationRequest": ["search-type"],
        "Observation": ["search-type"],
    }
    return {
        "resourceType": "CapabilityStatement",
        "status": "active",
        "kind": "instance",
        "fhirVersion": "4.0.1",
        "format": ["json"],
        "rest": [
            {
                "mode": "server",
                "resource": [
                    {"type": kind, "interaction": [{"code": code} for code in codes]}
                    for kind, codes in kinds.items()
                ],
            }
        ],
    }


def fixture_resources():
    patient = resource(
        "Patient",
        PATIENT_ID,
        birthDate="1980-01-02",
        telecom=[{"system": "phone", "value": "+1-202-555-0100"}],
    )
    schedule = resource(
        "Schedule",
        "synthetic-schedule-1",
        active=True,
        actor=[
            {"reference": "Practitioner/synthetic-provider-a"},
            {"reference": "Location/synthetic-clinic"},
        ],
    )
    slots = [
        resource(
            "Slot",
            f"synthetic-slot-{i + 1}",
            status="free",
            schedule={"reference": "Schedule/synthetic-schedule-1"},
            start=f"2026-11-23T{hour}:00:00+00:00",
            end=f"2026-11-23T{hour}:30:00+00:00",
        )
        for i, hour in enumerate((14, 15))
    ]
    appointment = resource(
        "Appointment",
        "synthetic-existing-appointment",
        status="booked",
        start="2026-11-23T12:00:00+00:00",
        end="2026-11-23T12:30:00+00:00",
        slot=[{"reference": "Slot/synthetic-existing-slot"}],
        participant=[
            {"actor": {"reference": f"Patient/{PATIENT_ID}"}, "status": "accepted"},
            {"actor": {"reference": "Practitioner/synthetic-provider-a"}, "status": "accepted"},
            {"actor": {"reference": "Location/synthetic-clinic"}, "status": "accepted"},
        ],
    )
    return {
        "Patient": [patient],
        "Schedule": [schedule],
        "Slot": slots,
        "Appointment": [appointment],
        "Condition": [
            resource(
                "Condition",
                "synthetic-condition-1",
                subject={"reference": f"Patient/{PATIENT_ID}"},
                code={"coding": [{"code": "synthetic-condition"}]},
            )
        ],
        "MedicationRequest": [],
        "Observation": [],
    }


def bundle(resources):
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(resources),
        "entry": [{"resource": r} for r in resources],
    }


app = FastAPI(title="Synthetic FHIR fixture server (not OpenEMR)")


@app.get("/fhir/metadata")
def metadata():
    return capability_statement()


@app.get("/fhir/{kind}/{id}")
def read_resource(kind: str, id: str):
    item = next((r for r in fixture_resources().get(kind, []) if r["id"] == id), None)
    if item is None:
        raise HTTPException(404, "Synthetic resource not found")
    return item


@app.get("/fhir/{kind}")
def search_resource(kind: str, patient: str | None = None, status: str | None = None):
    resources = fixture_resources()
    if kind not in resources:
        raise HTTPException(404, "Synthetic resource type not found")
    if patient and patient != PATIENT_ID:
        return bundle([])
    rows = resources[kind]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return bundle(rows)
