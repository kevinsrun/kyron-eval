"""One-shot private outbox claim for the explicitly authorized Codex Gmail bridge."""

import json
import os
import time
from datetime import UTC, datetime

from kyron_eval.ehr.config import openemr_adapter, openemr_directory


def main():
    local = openemr_directory()
    folder = local / "connector-outbox"
    folder.mkdir(exist_ok=True)
    target = json.loads((local / "booking-inventory.json").read_text())["authorized_recipient"]
    end = time.monotonic() + min(240, int(os.environ.get("KYRON_CONNECTOR_WAIT_SECONDS", "45")))
    while time.monotonic() < end:
        for path in sorted(folder.glob("*.request.json")):
            claim = path.with_suffix(".claimed")
            if claim.exists():
                continue
            request = json.loads(path.read_text())
            receipt = request["verified_receipt"]
            if request["message"]["to"] != target or receipt["status"] != "EHR_CONFIRMED":
                continue
            provider = openemr_adapter()
            try:
                read = provider.get_appointments(receipt["patient_id"])
                aid = receipt["appointment"]["appointment_id"]
                rows = [
                    r
                    for r in read.data.get("bookings", [])
                    if r["appointment_id"] == aid
                    and r["patient_id"] == receipt["patient_id"]
                    and r["confirmation_event_id"] == receipt["consent_event_id"]
                ]
                if not read.ok or not read.authoritative or len(rows) != 1:
                    continue
            finally:
                provider.close()
            try:
                fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                continue
            with os.fdopen(fd, "w") as out:
                out.write(datetime.now(UTC).isoformat())
            request["result_path"] = str(path.with_name(request["attempt_id"] + ".result.json"))
            request.pop("verified_receipt")
            print(json.dumps(request))
            return
        time.sleep(0.25)
    print("{}")


if __name__ == "__main__":
    main()
