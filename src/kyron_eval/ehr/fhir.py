"""Read-only FHIR R4 adapter. No write method ever sends an HTTP mutation.

Adapted conceptually from Guava's OpenEMRDemoAdapter: explicit HTTP boundary,
two-factor identity comparison and normalized clinical codes. Adds discovery,
synthetic-resource enforcement, completeness checks and bounded authenticated reads.
"""

import ipaddress
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from clinical_triage.domain.ehr import ClinicalContext, IdentityVerification
from clinical_triage.domain.scheduling import (
    AppointmentSlot,
    AvailabilityKind,
    AvailabilitySnapshot,
)
from kyron_eval.ehr.contracts import OPERATIONS, EHRResult
from kyron_eval.models import Booking

SYNTHETIC_TAG = {"system": "urn:kyron:synthetic", "code": "synthetic"}
ID = re.compile(r"^synthetic-[A-Za-z0-9.-]+$")


class FHIRFailure(Exception):
    def __init__(self, code, status="UNKNOWN"):
        self.code, self.status = code, status


class FHIRAdapter:
    name = "fhir"

    def __init__(
        self,
        base_url,
        *,
        bearer_token=None,
        patient_ids=("synthetic-patient-1",),
        sandbox_hosts=(),
        environment="local",
        timeout_seconds=5,
        verify_tls=True,
        client=None,
    ):
        parsed = urlsplit(base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("FHIR URL cannot contain credentials, query or fragment")
        host = parsed.hostname or ""
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback and not (environment == "synthetic-sandbox" and host in sandbox_hosts):
            raise ValueError(
                "FHIR endpoint must be loopback or an explicitly allowed synthetic sandbox"
            )
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError("External synthetic sandboxes require HTTPS")
        if verify_tls is False:
            raise ValueError("TLS verification cannot be disabled; configure a local CA instead")
        if not patient_ids or any(not ID.fullmatch(pid) for pid in patient_ids):
            raise ValueError("FHIR requires an explicit allowlist of synthetic patient IDs")
        self.base_url = base_url.rstrip("/")
        self.patient_ids = set(patient_ids)
        self._token = bearer_token
        self._client = client or httpx.Client(
            timeout=timeout_seconds, verify=verify_tls, follow_redirects=False, trust_env=False
        )
        self._owns_client = client is None
        self._resources = None
        self._discovery_error = None

    def _result(self, op, *, data=None, status="OK", code=None, authoritative=False):
        return EHRResult(
            operation=op,
            source=self.name,
            status=status,
            data=data or {},
            failure_code=code,
            authoritative=authoritative,
            observed_at=datetime.now(UTC),
        )

    def _run(self, op, callback):
        try:
            data = callback()
            return self._result(op, data=data, authoritative=True)
        except FHIRFailure as exc:
            return self._result(op, status=exc.status, code=exc.code)
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            IndexError,
            StopIteration,
            ValidationError,
        ):
            return self._result(op, status="UNKNOWN", code="FHIR_MALFORMED_EVIDENCE")

    def _get(self, path, *, params=None):
        headers = {"Accept": "application/fhir+json", "Cache-Control": "no-cache"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            response = self._client.get(
                f"{self.base_url}/{path}", headers=headers, params=params, follow_redirects=False
            )
        except httpx.TimeoutException as exc:
            raise FHIRFailure("FHIR_READ_TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise FHIRFailure("FHIR_CONNECTION_FAILED") from exc
        if response.status_code in {401, 403}:
            raise FHIRFailure("FHIR_AUTHENTICATION_FAILED", "ERROR")
        if response.status_code in {404, 405, 501}:
            raise FHIRFailure("FHIR_OPERATION_UNSUPPORTED", "UNSUPPORTED")
        if not 200 <= response.status_code < 300:
            raise FHIRFailure("FHIR_READ_REJECTED", "ERROR")
        if int(response.headers.get("Age", "0")) > 30:
            raise FHIRFailure("FHIR_STALE_RESPONSE")
        payload = response.json()
        if not isinstance(payload, dict):
            raise FHIRFailure("FHIR_INVALID_RESPONSE")
        return payload

    def _discover(self):
        if self._resources is not None:
            return
        self._resources = {}
        try:
            statement = self._get("metadata")
            if statement.get("resourceType") != "CapabilityStatement":
                raise FHIRFailure("FHIR_CAPABILITY_STATEMENT_REQUIRED")
            if not str(statement.get("fhirVersion", "")).startswith("4.0."):
                raise FHIRFailure("FHIR_R4_REQUIRED", "UNSUPPORTED")
            for rest in statement.get("rest", []):
                if rest.get("mode") == "server":
                    for resource in rest.get("resource", []):
                        self._resources[resource["type"]] = {
                            i["code"] for i in resource.get("interaction", [])
                        }
        except FHIRFailure as exc:
            self._discovery_error = exc
        except (ValueError, KeyError, TypeError, AttributeError):
            self._discovery_error = FHIRFailure("FHIR_INVALID_CAPABILITIES")

    def capabilities(self):
        self._discover()
        resources = self._resources

        def readable(kind):
            return "read" in resources.get(kind, set())

        def searchable(kind):
            return "search-type" in resources.get(kind, set())

        operations = {op: False for op in OPERATIONS}
        operations.update(
            {
                "get_patient": readable("Patient"),
                "verify_identity": readable("Patient"),
                "get_appointments": searchable("Appointment"),
                "get_availability": searchable("Slot") and readable("Schedule"),
                "get_clinical_context": all(
                    searchable(k) for k in ("Condition", "MedicationRequest", "Observation")
                ),
            }
        )
        return {
            "adapter": self.name,
            "version": "fhir-readonly-1.1",
            "synthetic": True,
            "connection": "connected" if self._discovery_error is None else "unavailable",
            "failure_code": self._discovery_error.code if self._discovery_error else None,
            "operations": operations,
            "readonly": True,
            "resources": {k: sorted(v) for k, v in resources.items()},
            "persistence": "Read-only endpoint; writes and review queue are unsupported",
        }

    def _require(self, resource, interaction):
        self._discover()
        if self._discovery_error:
            raise self._discovery_error
        if interaction not in self._resources.get(resource, set()):
            raise FHIRFailure("FHIR_CAPABILITY_UNSUPPORTED", "UNSUPPORTED")

    def _patient_allowed(self, patient_id):
        if patient_id not in self.patient_ids:
            raise FHIRFailure("FHIR_PATIENT_NOT_ALLOWLISTED", "ERROR")

    def _synthetic(self, resource):
        if not ID.fullmatch(str(resource.get("id", ""))) or not any(
            tag.get("system") == SYNTHETIC_TAG["system"]
            and tag.get("code") == SYNTHETIC_TAG["code"]
            for tag in resource.get("meta", {}).get("tag", [])
        ):
            raise FHIRFailure("FHIR_SYNTHETIC_MARKER_REQUIRED", "ERROR")
        return resource

    def _patient_resource(self, patient_id):
        self._patient_allowed(patient_id)
        self._require("Patient", "read")
        resource = self._synthetic(self._get(f"Patient/{patient_id}"))
        if resource.get("resourceType") != "Patient" or resource["id"] != patient_id:
            raise FHIRFailure("FHIR_PATIENT_MISMATCH", "ERROR")
        return resource

    def _search(self, kind, params):
        self._require(kind, "search-type")
        bundle = self._get(
            kind, params={**params, "_count": "100", "_tag": "urn:kyron:synthetic|synthetic"}
        )
        if bundle.get("resourceType") != "Bundle" or bundle.get("type") != "searchset":
            raise FHIRFailure("FHIR_SEARCHSET_REQUIRED")
        if any(link.get("relation") == "next" for link in bundle.get("link", [])):
            raise FHIRFailure("FHIR_INCOMPLETE_BUNDLE")
        resources = [entry["resource"] for entry in bundle.get("entry", [])]
        if bundle.get("total", len(resources)) != len(resources):
            raise FHIRFailure("FHIR_INCOMPLETE_BUNDLE")
        for resource in resources:
            self._synthetic(resource)
            if resource.get("resourceType") != kind:
                raise FHIRFailure("FHIR_RESOURCE_TYPE_MISMATCH")
        return resources

    def _reference(self, reference, kind):
        prefix = f"{kind}/"
        if not reference.startswith(prefix) or not ID.fullmatch(reference[len(prefix) :]):
            raise FHIRFailure("FHIR_SYNTHETIC_REFERENCE_REQUIRED", "ERROR")
        return reference[len(prefix) :]

    def get_patient(self, patient_id):
        def read():
            resource = self._patient_resource(patient_id)
            return {"patient_id": resource["id"], "synthetic": True}

        return self._run("get_patient", read)

    def verify_identity(self, patient_id, birth_date, phone):
        def read():
            patient = self._patient_resource(patient_id)

            def normalize(value):
                return re.sub(r"\D", "", str(value))[-10:]

            factors = tuple(
                code
                for code, matched in (
                    ("BIRTH_DATE", patient.get("birthDate") == birth_date),
                    (
                        "PHONE",
                        len(normalize(phone)) == 10
                        and any(
                            t.get("system") == "phone"
                            and normalize(t.get("value", "")) == normalize(phone)
                            for t in patient.get("telecom", [])
                        ),
                    ),
                )
                if matched
            )
            return IdentityVerification(
                patient_id=patient_id,
                verified=len(factors) == 2,
                verified_factor_codes=factors,
                requires_human_review=len(factors) != 2,
            ).model_dump(mode="json")

        return self._run("verify_identity", read)

    def _actors(self, entries):
        refs = [entry.get("actor", entry).get("reference", "") for entry in entries]
        practitioner = next(r for r in refs if r.startswith("Practitioner/"))
        location = next(r for r in refs if r.startswith("Location/"))
        return self._reference(practitioner, "Practitioner"), self._reference(location, "Location")

    def get_appointments(self, patient_id):
        def read():
            self._patient_allowed(patient_id)
            resources = self._search("Appointment", {"patient": patient_id})
            bookings = []
            for resource in resources:
                if resource.get("status") in {"cancelled", "entered-in-error"}:
                    continue
                if resource.get("status") not in {"booked", "arrived", "fulfilled", "checked-in"}:
                    raise FHIRFailure("FHIR_AMBIGUOUS_APPOINTMENT_STATUS")
                patient_refs = [
                    p.get("actor", {}).get("reference")
                    for p in resource.get("participant", [])
                    if p.get("actor", {}).get("reference", "").startswith("Patient/")
                ]
                if patient_refs != [f"Patient/{patient_id}"]:
                    raise FHIRFailure("FHIR_APPOINTMENT_PATIENT_MISMATCH", "ERROR")
                provider, location = self._actors(resource["participant"])
                slot_id = self._reference(resource["slot"][0]["reference"], "Slot")
                slot = AppointmentSlot(
                    slot_id=slot_id,
                    provider_id=provider,
                    location_id=location,
                    starts_at=resource["start"],
                    ends_at=resource["end"],
                    kind=AvailabilityKind.OPEN,
                )
                bookings.append(
                    Booking(
                        appointment_id=resource["id"],
                        patient_id=patient_id,
                        slot=slot,
                        confirmation_event_id="external-fhir-record",
                    ).model_dump(mode="json")
                )
            return {"bookings": bookings}

        return self._run("get_appointments", read)

    def get_availability(self, now):
        def read():
            resources = self._search("Slot", {"status": "free", "start": f"ge{now.isoformat()}"})
            slots = []
            for resource in resources:
                if resource.get("status") != "free":
                    raise FHIRFailure("FHIR_AVAILABILITY_STATUS_MISMATCH")
                schedule_id = self._reference(resource["schedule"]["reference"], "Schedule")
                self._require("Schedule", "read")
                schedule = self._synthetic(self._get(f"Schedule/{schedule_id}"))
                if schedule.get("resourceType") != "Schedule" or schedule.get("id") != schedule_id:
                    raise FHIRFailure("FHIR_SCHEDULE_MISMATCH")
                provider, location = self._actors(schedule["actor"])
                slots.append(
                    AppointmentSlot(
                        slot_id=resource["id"],
                        provider_id=provider,
                        location_id=location,
                        starts_at=resource["start"],
                        ends_at=resource["end"],
                        kind=AvailabilityKind.OPEN,
                    )
                )
            snapshot = AvailabilitySnapshot(
                snapshot_id="synthetic-fhir-snapshot",
                observed_at=now,
                expires_at=now + timedelta(seconds=30),
                slots=tuple(slots),
            )
            return {"snapshot": snapshot.model_dump(mode="json")}

        return self._run("get_availability", read)

    def get_clinical_context(self, patient_id):
        def read():
            self._patient_allowed(patient_id)
            codes = {}
            for kind, field in (
                ("Condition", "condition_codes"),
                ("MedicationRequest", "medication_codes"),
                ("Observation", "observation_codes"),
            ):
                resources = self._search(kind, {"patient": patient_id})
                normalized = []
                for resource in resources:
                    subject = resource.get("subject", {}).get("reference")
                    if subject != f"Patient/{patient_id}":
                        raise FHIRFailure("FHIR_CLINICAL_PATIENT_MISMATCH", "ERROR")
                    concept = resource.get(
                        "medicationCodeableConcept" if kind == "MedicationRequest" else "code", {}
                    )
                    normalized.extend(
                        str(c["code"]) for c in concept.get("coding", []) if c.get("code")
                    )
                codes[field] = tuple(normalized)
            return {
                **ClinicalContext(patient_id=patient_id, **codes).model_dump(
                    mode="json", exclude_unset=True
                ),
                "available_sections": list(codes),
                "unavailable_sections": ["allergy_codes"],
                "scope": "selected clinical codes only; not a complete clinical record",
            }

        return self._run("get_clinical_context", read)

    def _unsupported(self, operation):
        return self._result(
            operation, status="UNSUPPORTED", code="FHIR_READ_ONLY_MUTATION_UNSUPPORTED"
        )

    def create_appointment(self, *, patient_id, confirmation, idempotency_key):
        return self._unsupported("create_appointment")

    def cancel_appointment(self, patient_id, appointment_id):
        return self._unsupported("cancel_appointment")

    def create_human_review_request(self, patient_id, reason_code):
        return self._unsupported("create_human_review_request")

    def read_state(self, patient_id, now):
        appointments = self.get_appointments(patient_id)
        # Appointment evidence is retained; review-queue state is unavailable.
        return self._result(
            "read_state",
            status="UNKNOWN",
            code="FHIR_REVIEW_READBACK_UNSUPPORTED",
            data={
                "appointments": appointments.model_dump(mode="json"),
                "unavailable_collections": ["reviews"],
                "available_collections": ["bookings"] if appointments.ok else [],
            },
        )

    def close(self):
        if self._owns_client:
            self._client.close()
