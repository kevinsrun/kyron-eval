"""Seed only this newly provisioned synthetic clinic, using authenticated standard APIs."""

import json
import ssl
from datetime import UTC, datetime

import httpx
from setup import LOCAL, private_json

BASE = "https://localhost:9310"


def seed():
    token = json.loads((LOCAL / "token.json").read_text())["access_token"]
    evidence = []
    with httpx.Client(
        verify=ssl.create_default_context(cafile=str(LOCAL / "tls/server.pem")),
        timeout=20,
        trust_env=False,
        headers={"Authorization": f"Bearer {token}"},
    ) as c:

        def request(method, path, body=None, params=None):
            r = c.request(method, BASE + "/apis/default/" + path, json=body, params=params)
            payload = r.json() if r.content else {}
            if (
                r.status_code == 404
                and path.startswith("api/patient/")
                and path.endswith("/appointment")
            ):
                # Empty legacy lists return 404; FHIR must independently prove emptiness.
                patient = next(p for p in patients.values() if p["pid"] == path.split("/")[2])
                check = request("GET", "fhir/Appointment", params={"patient": patient["uuid"]})
                if check.get("total") == 0 and not check.get("entry"):
                    evidence.append(
                        {
                            "method": method,
                            "path": path,
                            "http_status": 404,
                            "interpretation": "FHIR independently proved empty",
                        }
                    )
                    return {"data": []}
                raise RuntimeError("Legacy empty response could not be verified")
            if isinstance(payload, list):
                payload = {"data": payload}
            evidence.append(
                {
                    "method": method,
                    "path": path,
                    "http_status": r.status_code,
                    "synthetic_request": body,
                    "response": payload,
                }
            )
            if (
                r.status_code >= 400
                or payload.get("validationErrors")
                or payload.get("internalErrors")
            ):
                raise RuntimeError(
                    f"Synthetic seed API failed: {method} {path} HTTP {r.status_code}"
                )
            return payload

        existing = request("GET", "api/patient")["data"]
        if any(not str(p.get("pubpid", "")).startswith("KYRON-SYNTHETIC-") for p in existing):
            raise RuntimeError("Refusing to seed a clinic containing unmarked patient records")
        facilities = request("GET", "api/facility")["data"]
        facility = facilities[0]
        request("PUT", "api/facility/" + facility["uuid"], {"name": "Kyron Synthetic Clinic"})
        users = request("GET", "api/user")["data"]
        provider = next(u for u in users if u["id"] == "1")
        request(
            "PUT",
            "api/practitioner/" + provider["uuid"],
            {"fname": "Synthetic", "lname": "Clinician"},
        )
        patients = {}
        for index, last in [(1, "PatientOne"), (2, "PatientTwo")]:
            pubpid = f"KYRON-SYNTHETIC-{index:03d}"
            body = {
                "fname": "KyronSynthetic",
                "lname": last,
                "DOB": "1980-01-02",
                "phone_home": "2025550100",
                "pubpid": pubpid,
                "sex": "Unknown",
            }
            if not any(p.get("pubpid") == pubpid for p in existing):
                request("POST", "api/patient", body)
            rows = request("GET", "api/patient", params={"pubpid": pubpid})["data"]
            rows = [p for p in rows if p.get("pubpid") == pubpid]
            if len(rows) != 1:
                raise RuntimeError("Synthetic patient seed is not unique")
            row = rows[0]
            patients[f"synthetic-patient-{index}"] = {
                "pid": str(row["pid"]),
                "uuid": row["uuid"],
                "pubpid": pubpid,
                "fname": body["fname"],
                "lname": last,
                "birth_date": body["DOB"],
                "phone": body["phone_home"],
            }
        manifest = {
            "synthetic_only": True,
            "instance": "kyron-openemr-synthetic",
            "openemr_version": "7.0.4",
            "base_url": BASE,
            "patients": patients,
            "provider": {"id": provider["id"], "uuid": provider["uuid"]},
            "facility": {"id": facility["id"], "uuid": facility["uuid"]},
            "timezone": "UTC",
        }
        patient = patients["synthetic-patient-1"]
        rows = request("GET", f"api/patient/{patient['pid']}/appointment")["data"]
        marker = "KYRON_SYNTHETIC_SEED"
        seeded = next((r for r in rows if r.get("pc_title") == marker), None)
        if not seeded:
            request(
                "POST",
                f"api/patient/{patient['pid']}/appointment",
                {
                    "pc_catid": "5",
                    "pc_title": marker,
                    "pc_duration": "1800",
                    "pc_hometext": marker,
                    "pc_apptstatus": "EMAIL",
                    "pc_eventDate": "2026-11-24",
                    "pc_startTime": "12:00",
                    "pc_facility": facility["id"],
                    "pc_billing_location": facility["id"],
                    "pc_aid": provider["id"],
                },
            )
            rows = request("GET", f"api/patient/{patient['pid']}/appointment")["data"]
            seeded = next(r for r in rows if r.get("pc_title") == marker)
        manifest["seeded_appointment_id"] = str(seeded["pc_eid"])
        manifest["created_at"] = datetime.now(UTC).isoformat()
        private_json(LOCAL / "manifest.json", manifest)
        private_json(LOCAL / "seed-evidence.json", {"synthetic": True, "events": evidence})
        print(
            "Synthetic clinic, two patients and a synthetic appointment "
            "seeded and read back through supported APIs."
        )
        print(
            json.dumps(
                {
                    "patients": {
                        alias: {"pid": p["pid"], "uuid": p["uuid"]} for alias, p in patients.items()
                    },
                    "seeded_appointment_id": manifest["seeded_appointment_id"],
                    "operations": len(evidence),
                }
            )
        )
