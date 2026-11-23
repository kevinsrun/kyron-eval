# Guarded booking and incoming phone demo

Separate branch `codex/booking-email`, following `79e4d2b`. The frozen take-home ZIP and original PR #1 are unchanged. The user explicitly authorized self-review for this final demonstration, superseding the earlier independent-review gate for the new demo. No independent approval, clinical certification or merge is claimed.

## Current supported path

Guava SDK 0.53.0 runs the same agent and backend with both `Runner.listen_webrtc` and the supported `Runner.listen_phone` interface. The authenticated account has an assigned number; it is loaded from private `GUAVA_AGENT_NUMBER`, checked against the account's actual assigned-number list, and shown in the dashboard. PSTN calls are accepted only for that destination. Caller ID never authenticates a patient. One encounter is admitted per launch; simultaneous/subsequent calls receive a busy response. Restart between calls. No outbound call is placed by this application.

Full name/DOB search plus the existing approved phone factor must succeed before appointment disclosure. The booking service supports only an administrative routine Office Visit, under the limited synthetic policy. Unsupported structured triage or any latched red flag prevents booking. This is not general symptom triage or clinical validation.

The two test options are operator-controlled inventory reserved for Avery Brooks at Willow Creek Clinic, November 24, 2026, 10:00 and 10:30 AM Eastern. This is a separate explicit clinic schedule source, **not OpenEMR Slot/Schedule discovery** and not a deduction from blank calendar cells. Before offers and again immediately before booking, the provider reads all marked clinic appointments and checks provider/time overlap. An incomplete read, changed inventory, stale offer, or conflict blocks the operation. The configured private inventory binds the actual provider/facility UUIDs and supported Office Visit type. Other writers must not independently allocate this exclusive test inventory.

The state machine enforces identity → administrative routing → permitted scheduling → options → selected option → confirmation → write → independent EHR readback → email → complete. A selected-payload hash binds natural yes/no confirmation to the current exact selection. The backend requires actual SDK agent speech containing the selected time/date/type/provider/location and recipient, followed by an explicit booking/email confirmation question. A natural “yes” then grants fresh consent to that exact selection; a yes to an unrelated question or a model boolean cannot authorize booking. Revisions revoke pending consent; identity/session changes invalidate it. Consent and offers expire.

A process lock plus durable SQLite reservation serializes this controlled inventory. Pending/unknown writes retain the reservation and are never automatically retried. OpenEMR's existing mutation journal supplies a second idempotency boundary. The service independently compares patient, appointment ID, slot, confirmation event, actor bindings and actual display fields. Only then does it write a new immutable confirmation evidence file and permit notification. Physical cancellation and rescheduling remain disabled.

## Email boundary

The requested test recipient is held in private configuration. **No outbound SMTP/provider credentials are currently configured.** The fallback local transport is Mailpit on [127.0.0.1:9325](http://127.0.0.1:9325), SMTP port 9326; both are bound to loopback. A message addressed to Gmail in Mailpit is still a local capture and does not reach Gmail. Final speech must say this explicitly. SMTP acknowledgment alone never establishes inbox delivery. The current demo can additionally use the authorized connected Personal Gmail account through the Codex-assisted outbox bridge. A live authorized dispatcher must be running; this is not a standalone SMTP daemon. It rechecks the EHR receipt before sending, claims each attempt once and records actual Gmail message ID and optional independent INBOX-label readback. Without the dispatcher, email stays QUEUED/UNKNOWN and no sent claim is made.

`ConfirmationEmail` supports explicitly configured SMTP with STARTTLS and credentials via `KYRON_EMAIL_OUTBOUND=1`, `KYRON_SMTP_HOST`, `KYRON_SMTP_PORT`, `KYRON_SMTP_FROM`, `KYRON_SMTP_USER`, and `KYRON_SMTP_PASSWORD`. This external path is unverified without configured credentials. Recipient authorization remains tied to the private inventory, and fresh caller confirmation covers both the exact appointment and address. Never paste credentials into source or the chat.

The message is constructed only from a verified EHR receipt: clinic, Office Visit type, timezone-aware date/time, clinician, location, native reference and clinic contact instructions. No symptoms, DOB or diagnoses are included. A durable appointment/recipient/notification key prevents repeated dispatch. SMTP 250 means accepted (`SENT`), never `DELIVERED`. Connection loss after DATA is `UNKNOWN` with no automatic retry; known rejection is `FAILED`. A confirmed booking remains confirmed if email fails.

Mailpit uses the official pinned `axllent/mailpit:v1.27.4` image, digest `sha256:df6c2541907e1be6fac21f509927cf6ed771617a1f4b361ef66d97bd05593d2d`; see [official Docker instructions](https://mailpit.axllent.org/docs/install/docker/). No relay is configured.

## Reliable startup

```sh
# Start once if the isolated capture container is absent:
docker run -d --name kyron-mailpit-synthetic \
  -p 127.0.0.1:9325:8025 -p 127.0.0.1:9326:1025 \
  axllent/mailpit:v1.27.4

# One-time private inventory configuration; use an explicitly authorized test address:
KYRON_OPENEMR_DIR=/path/to/private/.local \
  uv run --frozen python scripts/configure_booking_demo.py --recipient ADDRESS

./start-booking-demo.sh --preflight
./start-booking-demo.sh
```

The existing `start-voice-demo.sh` retains the read-only receptionist mode when `KYRON_BOOKING_ENABLED` is absent. Both launchers force the underlying adapter's default writes off; the guarded booking coordinator temporarily enables only its exact authorized creation. Direct generic voice actions still cannot mutate. Existing cancellation replay behavior is not used for this demo.

## Five-minute recording

Open [the synchronized dashboard](http://127.0.0.1:8003/voice-demo) in a desktop window wide enough for three panels. Call the assigned number shown there using the iPhone's normal Phone app. Do not press the browser microphone button for a phone call.

Use only the fictional identity: **Jordan Morgan / April 12, 1990 / 202-555-0122**. Request **“a routine checkup next Tuesday morning.”** Choose a returned option. Listen to the actual readback and answer the exact booking-and-email readback with a simple **“Yes”** or **“No.”** No secret code or memorized phrase is needed. The current connected Gmail route can send to the explicitly authorized real inbox; local capture remains a separate fallback. If the backend cannot establish complete evidence, stop and report the actual failure; do not narrate a successful booking.

Suggested outline: 0:00–0:40 establish fictional environment and claims/tool/state distinction; 0:40–2:40 make the real incoming call and confirm the exact option; 2:40–3:40 show the new actual appointment and independent readback; 3:40–4:30 show exact Email Preview and the real Mailpit message; 4:30–5:00 state local capture versus Gmail delivery, limited inventory/concurrency/clinical scope, and the pending independent review of PR #1. Finish recording and submit before **8 pm Eastern, November 22**.

A PSTN saved trace uses `GUAVA_PSTN_NETWORK_SPEECH` only after an actual PSTN network session and received caller/agent speech callbacks. This does not claim raw audio capture or a signed delivery receipt; audibility on the user's iPhone is separately confirmed by the user. WebRTC retains its existing distinct evidence class and browser audio observations. Protocol tests manually dispatching callbacks remain tests, never real phone evidence. No video is recorded or submitted by the assistant.

If phone connection, booking or email fails, retain the failed trace and use the already saved genuine read-only WebRTC calls as an explicitly historical fallback. Never substitute scripted callbacks and call them live audio.


## Observed final transaction and timezone

The natural yes/no PSTN call actually booked **appointment 88**, November 24, 2026, **10:00 AM EST** with Avery Brooks at Willow Creek Clinic. Fresh native/FHIR readback verified **15:00 UTC**, the same instant. The isolated OpenEMR global `gbl_time_zone` is UTC; its native calendar therefore shows 3 PM. Dashboard appointment cards/calendar now render America/New_York explicitly and label the OpenEMR link UTC. No appointment time was shifted.

The actual confirmation reached the authorized Personal Gmail inbox: provider message `1a205da3bc0d12ef`; a separate Gmail read returned SENT and INBOX. The initial spoken email status remained unverified because the human-orchestrated dispatcher acknowledgment arrived after its waiting deadline. That raw trace is retained unchanged. The recording dispatcher must run concurrently so provider acknowledgment reaches the agent before its final response. Do not call that original spoken response a sent confirmation. A public, recipient-redacted evidence copy is saved under `artifacts/booking-phone/verified-booking-and-inbox.json`; the full raw call remains local. The user separately confirmed receiving the email.

The first unsuccessful code-word call remains preserved with no write/email; the revised actual call used a simple Yes. The dashboard redesign uses existing routes, a sidebar and compact three-panel layout. Raw JSON and HTTP metadata remain expandable. Browser audio activity comes only from measured decoded RMS; PSTN shows network speech evidence and does not fabricate an amplitude animation. Appointments, historical run inspection and triage pages were browser-verified at 1440×900 without horizontal overflow.


Final verification: **272 passed** with the synthetic local OpenEMR integration suite enabled; **261 passed / 11 skipped** offline. Ruff lint and format checks passed; one existing Starlette/httpx deprecation warning remains. All prior artifacts and frozen ZIP were hash checked unchanged. Personal recipient details and the full phone transcripts remain local; the public proof is explicitly redacted and includes the original raw SHA-256. No PR #1 merge was performed. The separate recording dispatcher is limited to the active authorized Codex session; do not imply a standalone production email service.
