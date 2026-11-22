# Isolated real OpenEMR 7.0.4

This optional integration runs a real OpenEMR database containing only fabricated clinic, practitioner and patient records. Mock remains the application's default. No Guava service or existing OpenEMR installation is required or reused.

## Start from the repository root

Requires Docker Desktop/Engine with Compose, OpenSSL, Python 3.11+ and uv. Start Docker through its usual local controls, then:

```sh
uv sync --frozen
uv run python integrations/openemr/setup.py init
uv run python integrations/openemr/setup.py up
uv run python integrations/openemr/setup.py status
uv run python integrations/openemr/setup.py auth
uv run python integrations/openemr/setup.py seed
KYRON_OPENEMR_ALLOW_WRITES=1 KYRON_PORT=8002 ./start.sh
```

Open http://127.0.0.1:8002. Probe real local OpenEMR, or inspect **OpenEMR evidence** for saved actual results. Select the real adapter, **B · Verified** and **Real OpenEMR · cancel seeded synthetic appointment** for an actual agent mutation. This removes only that marked synthetic event. Run `setup.py seed` again before each new cancellation: a live database does not reset per agent. A+B against the same cancellation target is rejected. The original eight mock scenarios and mock fault controls remain unchanged.

`up` waits up to 360 seconds for health checks. First initialization can take several minutes. It does not silently patch the API or bypass failed setup. If startup fails, inspect only this Compose project's private logs; container startup/process diagnostics can contain test credentials, so never paste full logs, process arguments, environment or `docker inspect` output into artifacts. Re-running `up` preserves volumes. Do not target another OpenEMR instance.

The Compose project is `kyron-openemr-synthetic`. Only `127.0.0.1:9310:443` is published; MariaDB has no host port. A dedicated bridge and named volumes separate it from existing local services. It is not a public deployment or an egress-blocked network. No production data or credentials are permitted.

## Pinned images and actual source

| Component | Immutable image |
|---|---|
| OpenEMR | `openemr/openemr:7.0.4@sha256:265eae8463c8d23294bed200f2ddd6aae851fc2f7da5ad47ad981a87f3cd1067` |
| MariaDB | `mariadb:11.4.5@sha256:49117dcc565cf51aa57ac5fca59ab31213402ff0eae6ffc13c46a37b938f7e4b` |

The live CapabilityStatement reports OpenEMR **7.0.4**, FHIR **4.0.1**. Release routes were inspected at tag `v7_0_4`, commit `466c4a32aa633b98a382c35f45fd902688ed83e7`. The image supplies its standard routes unchanged; our mounted provisioning script registers only the isolated test client through OpenEMR's client repository. See [official release API guidance](https://www.open-emr.org/wiki/index.php/OpenEMR_7.0.4_API), [standard API](https://github.com/openemr/openemr/blob/master/Documentation/api/STANDARD_API.md), [authentication](https://github.com/openemr/openemr/blob/master/Documentation/api/AUTHENTICATION.md) and [authorization](https://github.com/openemr/openemr/blob/master/Documentation/api/AUTHORIZATION.md). Current upstream documents can change; the pinned release source and actual evidence determine this adapter's behavior.

## Authentication and private files

Initialization generates random database/admin test passwords and a seven-day localhost TLS certificate. `.local/` is ignored, directory mode 700; password/client/token/ledger/private-key files use mode 600. The adapter trusts only that explicit certificate, validates TLS and refuses redirects, arbitrary hosts or ports. No OS/browser trust store is changed. The OpenEMR browser UI may show a self-signed-certificate warning; the evaluation dashboard uses verified server-side API requests and needs no browser bypass.

The CLI registration follows the release's test-client repository mechanism and enables an OAuth user client. The isolated instance enables password grant solely for reproducible synthetic testing, then requests `/oauth2/default/token` over verified TLS as `synthetic_admin`. This is not a production authentication recommendation. `auth` reuses the private registered client and obtains a new token; the observed token lifetime is 3600 seconds. Re-run it when protected reads return authorization rejection. No silent renewal or mutation retry occurs.

Test scopes cover patient/appointment CRUD, facility/practitioner setup, user read and selected FHIR reads: `openid api:oemr api:fhir user/patient.crus user/appointment.cruds user/facility.crus user/practitioner.crus user/user.rs user/Patient.rs user/Appointment.rs user/Condition.rs user/MedicationRequest.rs user/Observation.rs`. OpenEMR's granted scope string omits the two API-enable selectors. Saved evidence records granted scopes, expiry, redacted auth method and protected-read authorization status; it never stores bearer/client secrets.

Never distribute `.local`, Docker volumes or logs. A fresh reviewer installation generates its own credentials, UUIDs and numeric IDs; saved historical artifacts retain the IDs actually observed. `KYRON_OPENEMR_DIR` can select another private directory for the app, but setup scripts deliberately use their own `.local` directory. Do not point the manifest at existing records.

## Actual operation boundaries

| Operation | API path under `/apis/default/` | Observed support |
|---|---|---|
| Capability discovery | `GET fhir/metadata` | OpenEMR 7.0.4 / FHIR 4.0.1 |
| Patient search | `GET fhir/Patient?identifier=KYRON-SYNTHETIC-001` | Complete allowlisted synthetic result |
| Patient retrieval / identity | `GET fhir/Patient/{uuid}` | Exact UUID, public marker, fabricated name, DOB and phone validation |
| Appointment read | `GET fhir/Appointment?patient={uuid}` and `GET api/patient/{pid}/appointment` | Independently reconcile UUIDs, patient, native IDs, time and actor references |
| Availability | Slot/Schedule absent from live capabilities | `UNSUPPORTED`; no free slots inferred from an empty calendar |
| Create appointment | `POST api/patient/{pid}/appointment` | Explicit write flag, synthetic provider/facility, native payload; response ID is acknowledgment only |
| Cancel appointment | `DELETE api/patient/{pid}/appointment/{eid}` | Physically deletes calendar event; patient-bound marked pre-read required; no general status-update implementation |
| Clinical context | FHIR Condition, MedicationRequest, Observation searches | Selected codes only; complete empty sections distinguished from unavailable allergy evidence |
| Human review request | No configured API | `UNSUPPORTED`; no simulated handoff |
| Full state | Authoritative appointments + unavailable review queue | Overall `UNKNOWN`; appointment-specific readback remains usable |

Seed setup uses documented standard patient POST/read plus facility/practitioner updates. It refuses preexisting non-Kyron patient records. Two patients are `KyronSynthetic PatientOne`/`PatientTwo`; all names, DOB/phone values and clinic/provider labels are fabricated. Native numeric IDs/UUIDs are mapped in the private manifest to existing synthetic workflow aliases.

Observed release quirks: FHIR searches return `collection` Bundles, so this dedicated adapter checks total/completeness for both collection and searchset. The legacy native empty appointment list returns 404 with an empty body; that alone is never absence proof. Invalid legacy POST fields can return **HTTP 200 with validation errors**; those decode as `ERROR/OPENEMR_VALIDATION_REJECTED`, not success. FHIR Appointment advertises only read/search; native writes are a separate API contract. The retained generic FHIRAdapter stays read only and retains its stricter tagged fixture contract.

## Readback, retries and idempotency

POST acknowledgment never establishes persistence. Creation readback must match a newly observed native appointment ID, patient, requested time, provider/facility and the synthetic consent correlation marker stored in the actual appointment title. Cancellation needs known prior existence plus independent authoritative final absence. A fresh adapter issues new FHIR/native requests; no private mock snapshot or local write ledger is treated as EHR state.

Safe GETs have at most two attempts for connection/timeouts or 502/503/504. POST and DELETE have one attempt. Missing/stale/incomplete evidence is UNKNOWN. An ambiguous write stays UNKNOWN until independent reconciliation; never blindly retry it. The SQLite journal stores a pending operation before HTTP, then its result. Same namespace/key/payload returns the saved result, conflicting payload is rejected and an unresolved pending entry remains UNKNOWN. To resume the same logical operation after a process restart, use the saved `operation_namespace` from capabilities with `openemr_adapter(operation_namespace=...)`. A new run has a new namespace. This local safeguard is not a global/server idempotency guarantee, and it cannot prevent duplicate user intent across separate runs.

## Reproduce checks and controlled evidence

```sh
uv run pytest -q
uv run pytest -q -m fixture_contract
uv run pytest -q -m llm_protocol
KYRON_RUN_OPENEMR_TESTS=1 KYRON_OPENEMR_ALLOW_WRITES=1 uv run pytest -q -m openemr_integration
uv run python integrations/openemr/setup.py seed
KYRON_OPENEMR_ALLOW_WRITES=1 uv run python scripts/openemr_experiment.py
```

Default tests never require Docker or network access. Live tests create and remove their own marked appointment. The experiment performs actual API operations and saves uniquely named artifacts. Its creation probes use an explicitly specified synthetic time, **not discovered free availability**, and can leave overlapping synthetic calendar records. They do not demonstrate successful agent booking. The real agent mutation demonstration is caller-confirmed cancellation. The optional LLM path is protocol-tested only; no actual model experiment has been run.

## Teardown

```sh
uv run python integrations/openemr/setup.py down
```

This stops/removes only our project containers and network while preserving its named volumes and private files for later restart. It does not stop Docker Desktop, unrelated containers or any existing EHR. Do not add `--volumes` unless intentionally discarding this newly created disposable clinic. TLS certificates expire after seven days; renew the isolated certificate deliberately before later reuse. Do not disable certificate verification.
