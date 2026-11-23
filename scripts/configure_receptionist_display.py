"""Reversible operator-only display names for the isolated fictional clinic."""

from datetime import UTC, datetime
from pathlib import Path

from kyron_eval.ehr.config import openemr_adapter
from kyron_eval.storage import atomic_json


def main():
    provider = openemr_adapter()
    try:
        patients = provider._request("GET", "api/patient")["data"]
        if any(not r.get("pubpid", "").startswith("KYRON-SYNTHETIC-") for r in patients):
            raise RuntimeError("Refusing to configure an unmarked clinic")
        actor = provider.manifest["provider"]["uuid"]
        facility = provider.manifest["facility"]["uuid"]
        before = {
            "actor": provider._request("GET", "api/user/" + actor),
            "facility": provider._request("GET", "api/facility/" + facility),
        }

        def display(value):
            value = value.get("data", value) if isinstance(value, dict) else value
            if isinstance(value, list):
                if len(value) != 1:
                    raise RuntimeError("Ambiguous display record")
                value = value[0]
            return {k: value.get(k) for k in ["uuid", "fname", "lname", "name"]}

        before = {k: display(v) for k, v in before.items()}
        if before["actor"]["uuid"] != actor or before["facility"]["uuid"] != facility:
            raise RuntimeError("Display record binding mismatch")
        provider._request(
            "PUT", "api/practitioner/" + actor, body={"fname": "Avery", "lname": "Brooks"}
        )
        provider._request("PUT", "api/facility/" + facility, body={"name": "Willow Creek Clinic"})
        after = {
            "actor": display(provider._request("GET", "api/user/" + actor)),
            "facility": display(provider._request("GET", "api/facility/" + facility)),
        }
        if after["actor"]["fname"] != "Avery" or after["facility"]["name"] != "Willow Creek Clinic":
            raise RuntimeError("Display changes not independently verified")
        path = Path("artifacts/receptionist") / (
            "operator-display-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + ".json"
        )
        atomic_json(
            path,
            {
                "execution": "OPERATOR_CONFIGURATION_NOT_AGENT_MUTATION",
                "fictional_only": True,
                "before": before,
                "after": after,
                "http_events": provider.http_events,
            },
        )
        print("Fictional display names persisted and read back; audit saved at", path)
    finally:
        provider.close()


if __name__ == "__main__":
    main()
