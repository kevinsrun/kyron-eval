"""Environment-only endpoint credentials. Public status never returns tokens."""

import os
import ssl

from kyron_eval.ehr.fhir import FHIRAdapter
from kyron_eval.ehr.mock import MockEHRAdapter


def create_adapter(name, scenario, faults=None):
    if name == "mock":
        return MockEHRAdapter(scenario.initial_state, scenario.tools, faults=faults)
    if name != "fhir":
        raise ValueError("Choose mock or fhir")
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
    return {
        "default_adapter": "mock",
        "fhir_configured": bool(os.environ.get("KYRON_FHIR_BASE_URL")),
        "fhir_token_configured": bool(os.environ.get("KYRON_FHIR_TOKEN")),
        "synthetic_only": True,
        "fhir_readonly": True,
    }
