# Guava Voice AI SDK Integration — Kyron Medical

This document records the design, implementation, verification, and operational boundaries of the Guava Voice AI agent integration within the Kyron Medical evaluation platform on branch `feature/guava-voice-integration`.

---

## 1. Executive Summary & Baseline

- **Repository:** `kevinsrun/kyron-eval` (Private)
- **Active Branch:** `feature/guava-voice-integration` (branched from `codex/openemr-integration` at `40b8130`)
- **Core Technology:** `guava-sdk==0.53.0` with Python 3.11.16
- **Test Suite Status:** 158 passed (all 149 default tests passing, 8 unit/integration voice tests passing, and all live OpenEMR tests passing)
- **Historical Preservation:** All 294 historical artifacts from commit `78b7b84` / `40b8130` remain byte-for-byte identical (verified by SHA256 in `voice-preservation-manifest.json`).

---

## 2. Architecture & Design

The voice agent integration bridges real Guava Voice AI agent mechanics with Kyron's deterministic evaluation pipeline and isolated OpenEMR EHR infrastructure:

```
[ Synthetic Voice Call / Inbound Event ]
                   │
                   ▼
       [ Guava Voice AI Agent ]
         (guava.Agent, Tasks, Checklists)
                   │
                   ▼
      [ KyronVoiceService Layer ]
         • Strict Pydantic contracts
         • Identity verification prerequisite
         • Explicit caller confirmation
         • Patient ID bound to session
         • Clinical disposition safety locks
                   │
                   ▼
     [ OpenEMR / Mock EHR Adapter ]
         • Authenticated FHIR/API operations
         • Idempotency journaling
                   │
                   ▼
   [ Authoritative Independent Readback ]
                   │
                   ▼
  [ Captured Voice/Tool/EHR Event Trace ]
                   │
                   ▼
 [ Automated Evaluator & Inspection Dashboard ]
```

### Components

1. **Strict Data Contracts (`src/kyron_eval/voice/contracts.py`)**:
   - `VoiceVerifyIdentityInput`: Requires patient ID, birth date (YYYY-MM-DD), and 10-digit phone number.
   - `VoiceCancelAppointmentInput`: Requires bound patient ID, target appointment ID, and boolean `confirmed_by_caller=True`.
   - `VoiceCreateAppointmentInput`: Requires bound patient ID, slot ID, and boolean `confirmed_by_caller=True`.
   - `VoiceErrorCode`: Machine-readable error codes (`IDENTITY_NOT_VERIFIED`, `EXPLICIT_CONFIRMATION_REQUIRED`, `PATIENT_MISMATCH`, `DISPOSITION_PROHIBITS_MUTATION`, `EHR_MUTATION_FAILED`, `EHR_READBACK_UNKNOWN`).
   - `VoiceToolResult`: Normalized envelope returning completion status, EHR status (`OK`, `ERROR`, `UNKNOWN`, `UNSUPPORTED`), failure code, and user message.

2. **Mediation Service (`src/kyron_eval/voice/service.py`)**:
   - `KyronVoiceService`: Ensures the voice agent cannot bypass clinical safety rules.
   - Identity verification is strictly enforced before any appointment query or cancellation.
   - Emergency or human review dispositions immediately lock all scheduling and cancellation actions.
   - All mutations require affirmative caller confirmation (`confirmed_by_caller=True`).

3. **Guava Voice Agent Configuration (`src/kyron_eval/voice/agent.py`)**:
   - `build_guava_agent(service)`: Instantiates a real `guava.Agent`.
   - Configures tasks (`identity`, `cancellation`) with strict checklists (`patient_birth_date`, `patient_phone`, `appointment_id`, `caller_confirmation`).
   - Hooks lifecycle handlers: `on_call_start`, `on_task_complete`, `on_action("cancel_appointment")`, `on_escalate`, and `on_session_end`.

4. **Event Adapter (`src/kyron_eval/voice/adapter.py`)**:
   - `GuavaVoiceAdapter`: Translates Guava SDK events (`InboundCallEvent`, `CallerSpeechEvent`, `AgentSpeechEvent`, `TaskCompletedEvent`, `ActionItemCompletedEvent`, `EscalateEvent`, `BotSessionEnded`) into Kyron event traces.
   - Supports interruption tracking on agent utterances.

5. **Runner & Pipeline Integration (`src/kyron_eval/voice/runner.py`)**:
   - `execute_guava_voice`: Drives the synthetic voice caller turns, feeds events into the Guava agent event pipeline, executes OpenEMR standard API mutations, runs independent FHIR readback, and invokes `evaluate(scenario, trace, clarity_version="clarity-ehr-3.3")`.

---

## 3. Evidence Class & Telephony Capability Boundary

- **Active Evidence Class:** `VOICE_LIVE_SYNTHETIC`
  - The voice agent SDK (`guava-sdk`) is executed natively in Python.
  - Call events, tasks, checklists, and callbacks run through real Guava classes and event dispatchers.
  - Backing mutations execute against real local OpenEMR 7.0.4 over TLS at `https://localhost:9310`.
  - Final state is verified through independent FHIR readback.

- **Telephony Limitation:**
  - `GUAVA_API_KEY` and `GUAVA_AGENT_NUMBER` are not configured in the host environment.
  - Live PSTN/WebRTC audio telephony cannot be established without active Guava credentials and telephony infrastructure.
  - In accordance with Kyron evidence integrity standards, **no live PSTN/WebRTC calls or fabricated audio streams are claimed**. The exact boundary is recorded as `VOICE_LIVE_SYNTHETIC`.

---

## 4. Observed Verification Artifacts

### Persisted Voice Run: `run-20261122T220620-3097cd9942`
- **Scenario:** `openemr-cancel-existing` (Real OpenEMR synthetic patient appointment cancellation)
- **Agent Variant:** `VOICE`
- **Evidence Class:** `VOICE_LIVE_SYNTHETIC`
- **Key Evaluator Outcomes:**
  - `ehr_write_verification`: **PASS** (Authoritative independent readback confirmed calendar event deletion)
  - `ehr_identity_binding`: **PASS** (Patient identity verified against synthetic fixture before mutation)
  - `ehr_claim_support`: **UNKNOWN** (Evaluator detected truthful voice agent response acknowledging completed cancellation)

### Test Verification Logs
- `artifacts/verification/voice-all-tests.txt`: 158 passed tests (including all unit, EHR fixture, and live OpenEMR tests).
- `artifacts/verification/voice-default-tests.txt`: 149 passed, 9 skipped (offline default test run).
- `artifacts/verification/voice-lint.txt`: Clean lint status (`All checks passed!`).
- `artifacts/verification/voice-preservation-manifest.json`: Verification of 294 historical artifacts.

---

## 5. Walkthrough Instructions

### Running Voice Tests
```sh
# Run default offline voice tests
.venv/bin/pytest -v tests/test_guava_voice.py

# Run live OpenEMR integration voice test (requires running OpenEMR stack)
KYRON_RUN_OPENEMR_TESTS=1 KYRON_OPENEMR_ALLOW_WRITES=1 .venv/bin/pytest -v tests/test_guava_voice.py
```

### Executing a Voice Run via CLI / Python
```python
from kyron_eval.replay import load_scenarios
from kyron_eval.storage import Store

store = Store()
run = store.execute(
    variants=["VOICE"],
    scenario_ids=["openemr-cancel-existing"],
    adapter_name="openemr",
)
print("Run ID:", run.id)
print("Results:", [r.metrics for r in run.results])
```

### Inspecting via Dashboard
1. Launch workbench: `./start.sh`
2. Open `http://localhost:8000`
3. Select scenario `Real OpenEMR · cancel seeded synthetic appointment`, choose `VOICE` agent mode, and click **Run**.
4. The dashboard displays the Guava Voice AI integration panel, caller/agent speech events, tool execution payloads, and authoritative EHR write verification outcomes.
