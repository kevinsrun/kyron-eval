"""Environment-only endpoint credentials. Public status never returns tokens."""

import json
import os
import ssl
from pathlib import Path

from kyron_eval.ehr.fhir import FHIRAdapter
from kyron_eval.ehr.mock import MockEHRAdapter


def create_adapter(name, scenario, faults=None):
    if name == "mock":
        return MockEHRAdapter(scenario.initial_state, scenario.tools, faults=faults)
    if name == "openemr":
        if faults:
            raise ValueError("Failure injection is supported only by the mock EHR")
        return openemr_adapter()
    if name != "fhir":
        raise ValueError("Choose mock, fhir, or isolated openemr")
    if faults:
        raise ValueError("Failure injection is supported only by the mock EHR")
    url = os.environ.get("KYRON_FHIR_BASE_URL")
    if not url:
        raise ValueError(
            "FHIR is not configured; set KYRON_FHIR_BASE_URL to a synthetic local/sandbox endpoint"
        )
    ca_file = os.environ.get("KYRON_FHIR_CA_FILE")
    return FHIRAdapter(
        url,
        bearer_token=os.environ.get("KYRON_FHIR_TOKEN"),
        patient_ids=tuple(
            os.environ.get("KYRON_FHIR_PATIENT_IDS", "synthetic-patient-1").split(",")
        ),
        sandbox_hosts=tuple(os.environ.get("KYRON_FHIR_SANDBOX_HOSTS", "").split(",")),
        environment=os.environ.get("KYRON_FHIR_ENVIRONMENT", "local"),
        verify_tls=ssl.create_default_context(cafile=ca_file) if ca_file else True,
    )


def configuration_status():
    local = openemr_directory()
    return {
        "default_adapter": "mock",
        "fhir_configured": bool(os.environ.get("KYRON_FHIR_BASE_URL")),
        "fhir_token_configured": bool(os.environ.get("KYRON_FHIR_TOKEN")),
        "synthetic_only": True,
        "fhir_readonly": True,
        "openemr_configured": (local / "manifest.json").exists()
        and (local / "token.json").exists(),
        "openemr_writes_authorized": os.environ.get("KYRON_OPENEMR_ALLOW_WRITES") == "1",
    }


def openemr_directory():
    return Path(os.environ.get("KYRON_OPENEMR_DIR", "integrations/openemr/.local"))


def openemr_adapter(*, operation_namespace=None):
    from kyron_eval.ehr.openemr import OpenEMRAdapter

    local = openemr_directory()
    try:
        manifest = json.loads((local / "manifest.json").read_text())
        token_response = json.loads((local / "token.json").read_text())
        token = token_response["access_token"]
        ca = ssl.create_default_context(cafile=str(local / "tls/server.pem"))
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError("Run the optional OpenEMR setup, auth and seed commands first") from exc
    return OpenEMRAdapter(
        manifest,
        token=token,
        verify_tls=ca,
        ledger_path=local / "idempotency.sqlite",
        allow_writes=os.environ.get("KYRON_OPENEMR_ALLOW_WRITES") == "1",
        auth_context={
            "method": "OAuth2 password grant (isolated synthetic clinic only)",
            "granted_scopes": token_response.get("scope", "not returned"),
            "expires_in_seconds": token_response.get("expires_in"),
            "credentials_redacted": True,
        },
        operation_namespace=operation_namespace,
    )
