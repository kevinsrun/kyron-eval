"""Operator setup: add a fictional named patient and existing appointment, never free slots."""

import json
from datetime import UTC, datetime
from pathlib import Path

from kyron_eval.ehr.config import openemr_adapter, openemr_directory
from kyron_eval.storage import atomic_json


def main():
    local = openemr_directory()
    manifest_path = local / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    backup = local / "manifest.before-receptionist.json"
    if not backup.exists():
        backup.write_bytes(manifest_path.read_bytes())
        backup.chmod(0o600)
    provider = openemr_adapter()
    started = len(provider.http_events)
    try:
        patients = provider._request("GET", "api/patient")["data"]
        if any(not str(r.get("pubpid", "")).startswith("KYRON-SYNTHETIC-") for r in patients):
            raise RuntimeError("Only the isolated marked clinic is permitted")
        marker = "KYRON-SYNTHETIC-RECEPTIONIST"
        body = {
            "fname": "Jordan",
            "lname": "Morgan",
            "DOB": "1990-04-12",
            "phone_home": "2025550122",
            "pubpid": marker,
            "sex": "Unknown",
        }
        matches = [r for r in patients if r.get("pubpid") == marker]
        if not matches:
            provider._request("POST", "api/patient", body=body)
            matches = provider._request("GET", "api/patient", params={"pubpid": marker})["data"]
            matches = [r for r in matches if r.get("pubpid") == marker]
        if len(matches) != 1:
            raise RuntimeError("Fictional patient marker is ambiguous")
        row = matches[0]
        if any(str(row.get(k)) != body[k] for k in ["fname", "lname", "DOB", "pubpid"]):
            raise RuntimeError("Fictional patient fields differ; no overwrite")
        alias = "synthetic-patient-receptionist"
        manifest["patients"][alias] = {
            "pid": str(row["pid"]),
            "uuid": row["uuid"],
            "pubpid": marker,
            "fname": body["fname"],
            "lname": body["lname"],
            "birth_date": body["DOB"],
            "phone": body["phone_home"],
        }
        provider.patient_map = manifest["patients"]
        appointments = provider.get_appointments(alias)
        if not appointments.ok:
            raise RuntimeError("Cannot verify existing appointments")
        title = "KYRON_SYNTHETIC_RECEPTIONIST_EXISTING"
        native = []
        if appointments.data["bookings"]:
            native = provider._request("GET", f"api/patient/{row['pid']}/appointment")
        target = next((r for r in native if r.get("pc_title") == title), None)
        if target is None:
            provider._request(
                "POST",
                f"api/patient/{row['pid']}/appointment",
                body={
                    "pc_catid": "5",
                    "pc_title": title,
                    "pc_duration": "1800",
                    "pc_hometext": (
                        "Fictional operator-seeded existing appointment; not availability"
                    ),
                    "pc_apptstatus": "EMAIL",
                    "pc_eventDate": "2026-11-25",
                    "pc_startTime": "15:00",
                    "pc_facility": manifest["facility"]["id"],
                    "pc_billing_location": manifest["facility"]["id"],
                    "pc_aid": manifest["provider"]["id"],
                },
            )
        final = provider.get_appointments(alias)
        if not final.ok or not final.authoritative or not final.data["bookings"]:
            raise RuntimeError("Operator seed readback not established")
        manifest["receptionist_fixture"] = {
            "patient_name": "Jordan Morgan",
            "birth_date": body["DOB"],
            "phone": body["phone_home"],
            "source": "fictional operator seed, not an agent booking or verified availability",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        manifest_path.chmod(0o600)
        evidence = {
            "created_at": datetime.now(UTC).isoformat(),
            "execution": "OPERATOR_SETUP",
            "fictional_only": True,
            "patient_name": "Jordan Morgan",
            "appointments": final.model_dump(mode="json"),
            "http_events": provider.http_events[started:],
            "not_agent_booking": True,
            "availability_established": False,
        }
        path = Path("artifacts/receptionist") / (
            "operator-seed-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + ".json"
        )
        atomic_json(path, evidence)
        print(
            json.dumps(
                {
                    "artifact": str(path),
                    "patient": "Jordan Morgan",
                    "appointment_ids": [r["appointment_id"] for r in final.data["bookings"]],
                }
            )
        )
    finally:
        provider.close()


if __name__ == "__main__":
    main()
