"""Real local OpenEMR 7.0.4: FHIR reads + separately authorized standard mutations.

Only an explicitly seeded isolated clinic manifest is accepted. Native IDs remain
in evidence, while existing synthetic aliases preserve domain/policy boundaries.
"""

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from clinical_triage.domain.scheduling import AppointmentSlot, AvailabilityKind
from kyron_eval.ehr.contracts import OPERATIONS, EHRResult
from kyron_eval.models import Booking


class OpenEMRFailure(Exception):
    def __init__(self, code, status="UNKNOWN"):
        self.code, self.status = code, status


class OpenEMRAdapter:
    name = "openemr"
    synthetic_model_safe = (
        True  # Only manifest-bound, marker-checked synthetic fields leave this adapter.
    )

    def __init__(
        self,
        manifest,
        *,
        token,
        verify_tls,
        ledger_path,
        allow_writes=False,
        client=None,
        auth_context=None,
        operation_namespace=None,
    ):
        self.manifest = manifest
        url = urlsplit(manifest.get("base_url", ""))
        if (
            url.scheme != "https"
            or url.hostname not in {"localhost", "127.0.0.1"}
            or url.port != 9310
            or url.path
            or url.query
            or url.fragment
            or url.username
            or url.password
        ):
            raise ValueError("OpenEMR must be the isolated HTTPS loopback instance on port 9310")
        if (
            manifest.get("instance") != "kyron-openemr-synthetic"
            or manifest.get("synthetic_only") is not True
        ):
            raise ValueError("An isolated synthetic clinic manifest is required")
        if manifest.get("openemr_version") != "7.0.4" or verify_tls is False or not token:
            raise ValueError(
                "Pinned OpenEMR 7.0.4, verified TLS and test OAuth authentication are required"
            )
        self.base_url = manifest["base_url"]
        self.patient_map = manifest["patients"]
        if not self.patient_map or any(
            not k.startswith("synthetic-") or not v["pubpid"].startswith("KYRON-SYNTHETIC-")
            for k, v in self.patient_map.items()
        ):
            raise ValueError("Only explicit synthetic patient mappings are permitted")
        self._token = token
        self._client = client or httpx.Client(
            timeout=10, verify=verify_tls, trust_env=False, follow_redirects=False
        )
        self._owns_client = client is None
        self.allow_writes = allow_writes
        self.namespace = operation_namespace or uuid4().hex
        if not re.fullmatch(r"[a-f0-9]{32}", self.namespace):
            raise ValueError("Operation namespace must be a persisted 32-character hexadecimal ID")
        self.ledger_path = Path(ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.ledger_path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, "
                "payload TEXT NOT NULL, result TEXT)"
            )
        self.ledger_path.chmod(0o600)
        self.http_events = []
        self._resources = None
        self._discovery_failure = None
        self.authorization = "not_checked"
        self.auth_context = {
            key: value
            for key, value in (auth_context or {}).items()
            if key in {"method", "granted_scopes", "expires_in_seconds"}
        }
        self.auth_context.setdefault("method", "OAuth2 Bearer")
        self.auth_context["credentials_redacted"] = True

    def _result(self, op, status="OK", data=None, code=None, authoritative=False):
        return EHRResult(
            operation=op,
            source=self.name,
            status=status,
            data=data or {},
            failure_code=code,
            authoritative=authoritative,
            observed_at=datetime.now(UTC),
        )

    def _run(self, op, function):
        try:
            return self._result(op, data=function(), authoritative=True)
        except OpenEMRFailure as exc:
            return self._result(op, exc.status, code=exc.code)
        except (ValueError, KeyError, TypeError, AttributeError, IndexError):
            return self._result(op, "UNKNOWN", code="OPENEMR_MALFORMED_EVIDENCE")

    def _request(self, method, path, *, body=None, params=None):
        if not path.startswith(("fhir/", "api/")) or ".." in path:
            raise ValueError("Only declared OpenEMR API paths are allowed")
        # One bounded retry for safe GET connection errors/503 only. Mutations are never retried.
        for attempt in range(2 if method == "GET" else 1):
            evidence = {
                "method": method,
                "path": "/apis/default/" + path,
                "parameters": params or {},
                "payload": body,
                "attempt": attempt + 1,
                "authorization": "OAuth2 Bearer (redacted)",
                "tls_verified": True,
                "at": datetime.now(UTC).isoformat(),
            }
            try:
                response = self._client.request(
                    method,
                    self.base_url + "/apis/default/" + path,
                    params=params,
                    json=body,
                    headers={
                        "Authorization": "Bearer " + self._token,
                        "Accept": "application/fhir+json"
                        if path.startswith("fhir/")
                        else "application/json",
                        "Cache-Control": "no-cache",
                    },
                    follow_redirects=False,
                )
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                evidence["outcome"] = (
                    "timeout" if isinstance(exc, httpx.TimeoutException) else "connection_error"
                )
                self.http_events.append(evidence)
                if method == "GET" and attempt == 0:
                    continue
                raise OpenEMRFailure(
                    "OPENEMR_READ_UNAVAILABLE"
                    if method == "GET"
                    else "OPENEMR_WRITE_OUTCOME_UNKNOWN"
                ) from exc
            evidence["http_status"] = response.status_code
            evidence["request_id"] = response.headers.get("x-request-id")
            self.http_events.append(evidence)
            if response.status_code in {401, 403}:
                self.authorization = "rejected"
                raise OpenEMRFailure("OPENEMR_AUTHORIZATION_REJECTED", "ERROR")
            if response.status_code in {502, 503, 504} and method == "GET" and attempt == 0:
                continue
            if response.status_code in {404, 405, 501}:
                raise OpenEMRFailure("OPENEMR_OPERATION_UNSUPPORTED", "UNSUPPORTED")
            if not 200 <= response.status_code < 300:
                raise OpenEMRFailure(
                    "OPENEMR_READ_REJECTED" if method == "GET" else "OPENEMR_WRITE_REJECTED",
                    "ERROR" if method == "GET" or 400 <= response.status_code < 500 else "UNKNOWN",
                )
            if path != "fhir/metadata":
                self.authorization = "verified"
            if int(response.headers.get("age", "0")) > 30:
                raise OpenEMRFailure("OPENEMR_STALE_READBACK")
            try:
                payload = response.json()
            except ValueError as exc:
                raise OpenEMRFailure("OPENEMR_INVALID_RESPONSE") from exc
            if isinstance(payload, dict) and (
                payload.get("validationErrors") or payload.get("internalErrors")
            ):
                raise OpenEMRFailure("OPENEMR_VALIDATION_REJECTED", "ERROR")
            if (
                method == "POST"
                and path.endswith("/appointment")
                and isinstance(payload, dict)
                and payload
                and all(str(field).startswith("pc_") for field in payload)
                and all(
                    isinstance(errors, dict) and any("::" in str(code) for code in errors)
                    for errors in payload.values()
                )
            ):
                # 7.0.4's legacy controller can return validation failures with HTTP 200.
                raise OpenEMRFailure("OPENEMR_VALIDATION_REJECTED", "ERROR")
            return payload
        raise OpenEMRFailure("OPENEMR_READ_UNAVAILABLE")

    def _discover(self):
        if self._resources is not None:
            return
        self._resources = {}
        try:
            data = self._request("GET", "fhir/metadata")
            if (
                data.get("resourceType") != "CapabilityStatement"
                or data.get("fhirVersion") != "4.0.1"
                or data.get("software", {}).get("name") != "OpenEMR"
                or data.get("software", {}).get("version") != "7.0.4"
            ):
                raise OpenEMRFailure("OPENEMR_VERSION_MISMATCH", "ERROR")
            self._resources = {
                r["type"]: [i["code"] for i in r.get("interaction", [])]
                for rest in data.get("rest", [])
                if rest.get("mode") == "server"
                for r in rest.get("resource", [])
            }
        except OpenEMRFailure as exc:
            self._discovery_failure = exc
        except (ValueError, KeyError, TypeError, AttributeError):
            self._discovery_failure = OpenEMRFailure("OPENEMR_CAPABILITIES_MALFORMED")

    def capabilities(self):
        self._discover()
        ops = {op: False for op in OPERATIONS}
        if not self._discovery_failure:
            ops.update(
                get_patient="read" in self._resources.get("Patient", []),
                verify_identity="read" in self._resources.get("Patient", []),
                get_appointments="search-type" in self._resources.get("Appointment", []),
                create_appointment=self.allow_writes,
                cancel_appointment=self.allow_writes,
                get_clinical_context=all(
                    "search-type" in self._resources.get(r, [])
                    for r in ["Condition", "MedicationRequest", "Observation"]
                ),
            )
        return {
            "adapter": self.name,
            "display_name": "Real local OpenEMR",
            "version": "openemr-adapter-1.1",
            "openemr_version": "7.0.4",
            "fhir_version": "4.0.1",
            "synthetic": True,
            "synthetic_model_safe": True,
            "connection": "unavailable" if self._discovery_failure else "connected",
            "authorization": self.authorization,
            "authentication_context": self.auth_context,
            "operations": ops,
            "fhir_resources": self._resources,
            "failure_code": self._discovery_failure.code if self._discovery_failure else None,
            "standard_api_writes_authorized": self.allow_writes,
            "operation_namespace": self.namespace,
            "support_evidence": {
                "FHIR": "live CapabilityStatement",
                "standard appointment writes": (
                    "pinned release routes + explicit local authorization; outcomes "
                    "verified independently"
                ),
            },
            "persistence": "Actual local OpenEMR database; only readback establishes a mutation",
            "availability_note": "No Slot/Schedule API in pinned release; no free slots inferred",
        }

    def _allowed(self, patient_id):
        if patient_id not in self.patient_map:
            raise OpenEMRFailure("OPENEMR_PATIENT_NOT_ALLOWLISTED", "ERROR")
        return self.patient_map[patient_id]

    def _patient(self, patient_id):
        patient = self._allowed(patient_id)
        self._discover()
        if self._discovery_failure:
            raise self._discovery_failure
        resource = self._request("GET", "fhir/Patient/" + patient["uuid"])
        if (
            resource.get("resourceType") != "Patient"
            or resource.get("id") != patient["uuid"]
            or not any(i.get("value") == patient["pubpid"] for i in resource.get("identifier", []))
            or not any(
                n.get("family") == patient["lname"] and n.get("given") == [patient["fname"]]
                for n in resource.get("name", [])
            )
        ):
            raise OpenEMRFailure("OPENEMR_SYNTHETIC_BINDING_REJECTED", "ERROR")
        return resource

    def get_patient(self, patient_id):
        def read():
            p = self._patient(patient_id)
            return {
                "patient_id": patient_id,
                "native_patient_uuid": p["id"],
                "native_patient_id": self._allowed(patient_id)["pid"],
                "synthetic": True,
                "marker_verified": True,
            }

        return self._run("get_patient", read)

    def search_patients(self, patient_id):
        def read():
            p = self._allowed(patient_id)
            self._patient(patient_id)
            rows = self._bundle("Patient", {"identifier": p["pubpid"]})
            if len(rows) != 1 or rows[0]["id"] != p["uuid"]:
                raise OpenEMRFailure("OPENEMR_PATIENT_SEARCH_AMBIGUOUS")
            return {
                "patients": [
                    {"patient_id": patient_id, "native_patient_uuid": p["uuid"], "synthetic": True}
                ]
            }

        return self._run("search_patients", read)

    def verify_identity(self, patient_id, birth_date, phone):
        def read():
            p = self._patient(patient_id)
            factors = []
            if p.get("birthDate") == birth_date:
                factors.append("BIRTH_DATE")
            if phone and any(
                re.sub(r"\D", "", t.get("value", "")) == re.sub(r"\D", "", phone)
                for t in p.get("telecom", [])
                if t.get("system") == "phone"
            ):
                factors.append("PHONE")
            return {
                "patient_id": patient_id,
                "verified": len(factors) == 2,
                "verified_factor_codes": factors,
                "synthetic_fixture_identity_only": True,
            }

        return self._run("verify_identity", read)

    def _bundle(self, kind, params):
        self._discover()
        if self._discovery_failure:
            raise self._discovery_failure
        if "search-type" not in self._resources.get(kind, []):
            raise OpenEMRFailure("OPENEMR_FHIR_RESOURCE_UNSUPPORTED", "UNSUPPORTED")
        data = self._request("GET", "fhir/" + kind, params={**params, "_count": "100"})
        entries = data.get("entry", [])
        if (
            data.get("resourceType") != "Bundle"
            or data.get("type") not in {"collection", "searchset"}
            or data.get("total") != len(entries)
            or any(link.get("relation") == "next" for link in data.get("link", []))
        ):
            raise OpenEMRFailure("OPENEMR_INCOMPLETE_READBACK")
        rows = [e["resource"] for e in entries]
        if any(r.get("resourceType") != kind for r in rows):
            raise OpenEMRFailure("OPENEMR_RESOURCE_TYPE_MISMATCH")
        return rows

    def _appointments(self, patient_id):
        p = self._allowed(patient_id)
        self._patient(patient_id)
        fhir = self._bundle("Appointment", {"patient": p["uuid"]})
        for row in fhir:
            patients = [
                x.get("actor", {}).get("reference")
                for x in row.get("participant", [])
                if x.get("actor", {}).get("reference", "").startswith("Patient/")
            ]
            if patients != ["Patient/" + p["uuid"]]:
                raise OpenEMRFailure("OPENEMR_APPOINTMENT_PATIENT_MISMATCH", "ERROR")
        # Empty legacy lists return 404; use a complete FHIR bundle instead.
        if not fhir:
            return [], []
        native = self._request("GET", f"api/patient/{p['pid']}/appointment")
        if not isinstance(native, list):
            raise OpenEMRFailure("OPENEMR_NATIVE_APPOINTMENTS_AMBIGUOUS")
        if {r["id"] for r in fhir} != {r["pc_uuid"] for r in native}:
            raise OpenEMRFailure("OPENEMR_CROSS_API_STATE_MISMATCH")
        records = []
        for raw in native:
            if (
                str(raw["pid"]) != p["pid"]
                or raw["puuid"] != p["uuid"]
                or not raw["pc_title"].startswith("KYRON_SYNTHETIC")
                or str(raw["pc_aid"]) != str(self.manifest["provider"]["id"])
                or str(raw["pc_facility"]) != str(self.manifest["facility"]["id"])
            ):
                raise OpenEMRFailure("OPENEMR_APPOINTMENT_BINDING_REJECTED", "ERROR")
            source = next(r for r in fhir if r["id"] == raw["pc_uuid"])
            actors = {x.get("actor", {}).get("reference") for x in source.get("participant", [])}
            if "Location/" + raw["facility_uuid"] not in actors or not (
                {"Person/" + raw["pce_aid_uuid"], "Practitioner/" + raw["pce_aid_uuid"]} & actors
            ):
                raise OpenEMRFailure("OPENEMR_CROSS_API_ACTOR_MISMATCH", "ERROR")
            if source.get("status") == "cancelled":
                continue
            if source.get("status") not in {"booked", "arrived", "checked-in", "fulfilled"}:
                raise OpenEMRFailure("OPENEMR_APPOINTMENT_STATUS_AMBIGUOUS")
            parts = raw["pc_title"].split("|")
            slot_id = parts[1] if len(parts) == 4 else f"synthetic-openemr-slot-{raw['pc_eid']}"
            confirmation = parts[2] if len(parts) == 4 else "external-openemr-record"
            slot = AppointmentSlot(
                slot_id=slot_id,
                provider_id="synthetic-provider-a",
                location_id="synthetic-clinic",
                starts_at=source["start"],
                ends_at=source["end"],
                kind=AvailabilityKind.OPEN,
            )
            # Independently compare FHIR and standard API persisted time fields.
            expected = raw["pc_eventDate"] + "T" + raw["pc_startTime"] + "+00:00"
            if slot.starts_at != datetime.fromisoformat(expected):
                raise OpenEMRFailure("OPENEMR_CROSS_API_TIME_MISMATCH")
            expected_end = raw["pc_eventDate"] + "T" + raw["pc_endTime"] + "+00:00"
            if slot.ends_at != datetime.fromisoformat(expected_end):
                raise OpenEMRFailure("OPENEMR_CROSS_API_TIME_MISMATCH")
            records.append(
                Booking(
                    appointment_id=str(raw["pc_eid"]),
                    patient_id=patient_id,
                    slot=slot,
                    confirmation_event_id=confirmation,
                ).model_dump(mode="json")
            )
        return records, native

    def get_appointments(self, patient_id):
        def read():
            records, raw = self._appointments(patient_id)
            return {
                "bookings": records,
                "native_identifiers": [
                    {
                        "appointment_id": str(r["pc_eid"]),
                        "appointment_uuid": r["pc_uuid"],
                        "patient_uuid": r["puuid"],
                    }
                    for r in raw
                ],
                "readback_sources": [
                    "FHIR Appointment search",
                    "standard patient appointment read",
                ],
            }

        return self._run("get_appointments", read)

    def get_availability(self, now):
        return self._result(
            "get_availability", "UNSUPPORTED", code="OPENEMR_SLOT_SCHEDULE_UNSUPPORTED"
        )

    def _mutation(self, op, key, body, action):
        encoded = json.dumps(body, sort_keys=True)
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT payload,result FROM operations WHERE key=?", (key,)
            ).fetchone()
            if prior:
                if prior[0] != encoded:
                    return self._result(op, "ERROR", code="OPENEMR_IDEMPOTENCY_CONFLICT")
                return (
                    EHRResult.model_validate_json(prior[1])
                    if prior[1]
                    else self._result(op, "UNKNOWN", code="OPENEMR_PRIOR_WRITE_UNRESOLVED")
                )
            db.execute("INSERT INTO operations(key,payload) VALUES (?,?)", (key, encoded))
        result = self._run(op, action)
        # Mutation ACKs are never authoritative receipts. Readback is a separate operation.
        result.authoritative = False
        with sqlite3.connect(self.ledger_path) as db:
            db.execute(
                "UPDATE operations SET result=? WHERE key=?", (result.model_dump_json(), key)
            )
        return result

    def create_appointment(self, *, patient_id, confirmation, idempotency_key):
        op = "create_appointment"
        if not self.allow_writes:
            return self._result(op, "UNSUPPORTED", code="OPENEMR_WRITES_NOT_AUTHORIZED")
        try:
            p = self._allowed(patient_id)
        except OpenEMRFailure as exc:
            return self._result(op, exc.status, code=exc.code)
        slot = confirmation.offer.slot
        if slot.provider_id != "synthetic-provider-a" or slot.location_id != "synthetic-clinic":
            return self._result(op, "ERROR", code="OPENEMR_TARGET_NOT_ALLOWLISTED")
        key = hashlib.sha256(
            (self.namespace + "|" + patient_id + "|" + idempotency_key).encode()
        ).hexdigest()
        title = (
            "KYRON_SYNTHETIC|"
            + slot.slot_id
            + "|"
            + confirmation.confirmation_event_id
            + "|"
            + key[:16]
        )
        if len(title) > 150:
            return self._result(op, "ERROR", code="OPENEMR_CORRELATION_TOO_LONG")
        body = {
            "pc_catid": "5",
            "pc_title": title,
            "pc_duration": str(int((slot.ends_at - slot.starts_at).total_seconds())),
            "pc_hometext": title,
            "pc_apptstatus": "EMAIL",
            "pc_eventDate": slot.starts_at.astimezone(UTC).date().isoformat(),
            "pc_startTime": slot.starts_at.astimezone(UTC).strftime("%H:%M"),
            "pc_facility": self.manifest["facility"]["id"],
            "pc_billing_location": self.manifest["facility"]["id"],
            "pc_aid": self.manifest["provider"]["id"],
        }

        def write():
            self._patient(patient_id)
            records, raw = self._appointments(patient_id)
            if any(r.get("pc_title") == title for r in raw):
                raise OpenEMRFailure("OPENEMR_EXISTING_REQUEST_REQUIRES_RECONCILIATION")
            response = self._request("POST", f"api/patient/{p['pid']}/appointment", body=body)
            if not isinstance(response, dict) or not str(response.get("id", "")).isdigit():
                raise OpenEMRFailure("OPENEMR_WRITE_ACK_AMBIGUOUS")
            return {
                "completed": True,
                "appointment_id": str(response["id"]),
                "correlation": key[:16],
                "confirmation_event_id": confirmation.confirmation_event_id,
                "availability_verified": False,
            }

        return self._mutation(op, key, body, write)

    def cancel_appointment(self, patient_id, appointment_id):
        op = "cancel_appointment"
        if not self.allow_writes:
            return self._result(op, "UNSUPPORTED", code="OPENEMR_WRITES_NOT_AUTHORIZED")
        try:
            p = self._allowed(patient_id)
        except OpenEMRFailure as exc:
            return self._result(op, exc.status, code=exc.code)
        if not str(appointment_id).isdigit():
            return self._result(op, "ERROR", code="OPENEMR_APPOINTMENT_TARGET_INVALID")

        def cancel():
            records, _ = self._appointments(patient_id)
            if not any(r["appointment_id"] == str(appointment_id) for r in records):
                raise OpenEMRFailure("OPENEMR_APPOINTMENT_NOT_BOUND", "ERROR")
            response = self._request(
                "DELETE", f"api/patient/{p['pid']}/appointment/{appointment_id}"
            )
            if response.get("message") != "record deleted":
                raise OpenEMRFailure("OPENEMR_CANCEL_ACK_AMBIGUOUS")
            return {
                "completed": True,
                "appointment_id": str(appointment_id),
                "mechanism": "standard API deletion, not a FHIR status update",
            }

        key = f"{self.namespace}|cancel|{patient_id}|{appointment_id}"
        return self._mutation(
            op, key, {"patient_id": patient_id, "appointment_id": str(appointment_id)}, cancel
        )

    def get_clinical_context(self, patient_id):
        def read():
            p = self._allowed(patient_id)
            self._patient(patient_id)
            codes = {}
            for kind, field in [
                ("Condition", "condition_codes"),
                ("MedicationRequest", "medication_codes"),
                ("Observation", "observation_codes"),
            ]:
                records = self._bundle(kind, {"patient": p["uuid"]})
                values = []
                for record in records:
                    if record.get("subject", {}).get("reference") != "Patient/" + p["uuid"]:
                        raise OpenEMRFailure("OPENEMR_CLINICAL_PATIENT_MISMATCH", "ERROR")
                    concept = record.get(
                        "medicationCodeableConcept" if kind == "MedicationRequest" else "code", {}
                    )
                    values.extend(
                        str(c["code"]) for c in concept.get("coding", []) if c.get("code")
                    )
                codes[field] = values
            return {
                "patient_id": patient_id,
                **codes,
                "available_sections": list(codes),
                "unavailable_sections": ["allergy_codes"],
                "scope": "selected coded resources only",
            }

        return self._run("get_clinical_context", read)

    def create_human_review_request(self, patient_id, reason_code):
        return self._result(
            "create_human_review_request", "UNSUPPORTED", code="OPENEMR_REVIEW_QUEUE_UNSUPPORTED"
        )

    def read_state(self, patient_id, now):
        appointments = self.get_appointments(patient_id)
        return self._result(
            "read_state",
            "UNKNOWN",
            data={
                "appointments": appointments.model_dump(mode="json"),
                "available_collections": ["bookings"] if appointments.ok else [],
                "unavailable_collections": ["reviews"],
            },
            code="OPENEMR_PARTIAL_STATE_ONLY",
        )

    def close(self):
        if self._owns_client:
            self._client.close()
