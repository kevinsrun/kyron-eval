"""Provider boundary: synthetic mock writes and explicitly read-only FHIR integration."""

from kyron_eval.ehr.contracts import EHRAdapter, EHRResult
from kyron_eval.ehr.mock import MockEHRAdapter

__all__ = ["EHRAdapter", "EHRResult", "MockEHRAdapter"]
