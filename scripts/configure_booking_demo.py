"""Explicit operator-owned fictional schedule; no OpenEMR free-time inference."""

import argparse
import json

from kyron_eval.ehr.config import openemr_adapter, openemr_directory
from kyron_eval.storage import atomic_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipient", required=True, help="Explicitly authorized test recipient")
    args = parser.parse_args()
    p = openemr_adapter()
    try:
        rows = p.get_receptionist_appointments("synthetic-patient-receptionist")
        if not rows.ok or not rows.authoritative or not rows.data["appointments"]:
            raise RuntimeError("Actual fictional clinic binding required")
        existing = rows.data["appointments"][0]
        if (
            existing["clinician"] != "Avery Brooks"
            or existing["location"] != "Willow Creek Clinic"
            or existing["appointment_type"] != "Office Visit"
        ):
            raise RuntimeError("Unexpected clinic configuration")
        config = {
            "authority": "OPERATOR_APPROVED_EXCLUSIVE_SYNTHETIC_CLINIC",
            "approved_by": "Operator configuration under authorized synthetic booking request",
            "restriction": (
                "Only these reserved test slots; no other writers in this "
                "namespace. Not OpenEMR Slot discovery."
            ),
            "provider_uuid": p.manifest["provider"]["uuid"],
            "facility_uuid": p.manifest["facility"]["uuid"],
            "clinician": existing["clinician"],
            "location": existing["location"],
            "appointment_type": existing["appointment_type"],
            "authorized_recipient": args.recipient,
            "email_mode": "LOCAL_CAPTURE_ONLY",
            "slots": [
                {
                    "slot_id": "synthetic-book-" + str(i),
                    "provider_id": "synthetic-provider-a",
                    "location_id": "synthetic-clinic",
                    "starts_at": start,
                    "ends_at": end,
                    "kind": "OPEN",
                }
                for i, (start, end) in enumerate(
                    [
                        ("2026-11-24T15:00:00Z", "2026-11-24T15:30:00Z"),
                        ("2026-11-24T15:30:00Z", "2026-11-24T16:00:00Z"),
                    ],
                    1,
                )
            ],
        }
        target = openemr_directory() / "booking-inventory.json"
        if target.exists() and json.loads(target.read_text()) != config:
            raise RuntimeError("Refusing to overwrite a different clinic inventory")
        atomic_json(target, config)
        target.chmod(0o600)
        print(
            "Configured two operator-controlled Office Visit slots; email "
            "capture only; private configuration saved."
        )
    finally:
        p.close()


if __name__ == "__main__":
    main()
