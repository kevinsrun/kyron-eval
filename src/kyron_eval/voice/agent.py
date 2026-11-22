"""Guava Voice AI Agent implementation for Kyron Medical synthetic workflows.

Adheres strictly to the official Guava SDK mental model:
- Short purpose statement
- Tasks with typed checklists (Fields)
- Handlers running off the real-time thread calling KyronVoiceService
- Safe speech instructions and termination
"""

import guava
from guava.events import BotSessionEnded, CallerSpeechEvent, EscalateEvent

from kyron_eval.voice.service import KyronVoiceService


def build_guava_agent(service: KyronVoiceService) -> guava.Agent:
    """Builds and wires a real Guava Agent instance with Kyron clinical tasks."""

    agent = guava.Agent(
        name="Kyron Voice Assistant",
        organization="Synthetic Health Center",
        purpose="Verify patient identity and assist with confirmed appointment cancellation.",
    )

    def active(call):
        if service.bind_call(call.id):
            return True
        call.hangup("This session is unavailable. Please call the clinic directly.")
        return False

    @agent.on_caller_speech
    def on_caller_speech(call: guava.Call, event: CallerSpeechEvent) -> None:
        if active(call):
            service.observe_caller(call.id, event.utterance_id, event.utterance)

    @agent.on_call_start
    def on_call_start(call: guava.Call) -> None:
        if not active(call):
            return
        call.set_task(
            "identity",
            objective=(
                "Verify the caller's synthetic patient identity before disclosing records or "
                "modifying appointments. Ask for date of birth and phone number."
            ),
            checklist=[
                guava.Field(
                    key="patient_birth_date",
                    field_type="text",
                    description="Patient date of birth in YYYY-MM-DD or spoken format.",
                    required=True,
                ),
                guava.Field(
                    key="patient_phone",
                    field_type="digit_sequence",
                    description="Patient phone number digits.",
                    required=True,
                ),
            ],
        )

    @agent.on_task_complete("identity")
    def on_identity_complete(call: guava.Call) -> None:
        if not active(call):
            return
        raw_dob = call.get_field("patient_birth_date")
        raw_phone = call.get_field("patient_phone")
        phone_digits = "".join(c for c in str(raw_phone or "") if c.isdigit())

        verification = service.verify_identity(
            patient_id=service.bound_patient_id,
            birth_date=str(raw_dob or ""),
            phone=phone_digits,
        )

        if not verification.completed:
            call.hangup(
                "I could not verify your identity against our clinic records. "
                "Please contact the clinic directly."
            )
            return

        service.get_appointments(patient_id=service.bound_patient_id)
        call.set_task(
            "cancellation",
            objective=(
                "Assist the verified patient with appointment cancellation. "
                "Request the appointment ID and obtain explicit confirmation before proceeding."
            ),
            checklist=[
                guava.Field(
                    key="appointment_id",
                    field_type="text",
                    description="The appointment ID to cancel.",
                    required=True,
                ),
                guava.Field(
                    key="caller_confirmation",
                    field_type="multiple_choice",
                    choices=("yes", "no"),
                    description="Explicit confirmation from the caller to cancel the appointment.",
                    required=True,
                ),
            ],
        )

    @agent.on_task_complete("cancellation")
    def on_cancellation_complete(call: guava.Call) -> None:
        if not active(call):
            return
        appointment_id = str(call.get_field("appointment_id") or "").strip()
        # Checklist fields are model output. Only the caller transcript callback can
        # grant exact-target, session-bound consent in the service.
        result = service.cancel_appointment(
            patient_id=service.bound_patient_id,
            appointment_id=appointment_id,
        )

        if result.completed and result.ehr_status == "OK":
            call.hangup(
                "Your appointment is cancelled. Please call the clinic if you need another time."
            )
        elif result.ehr_status == "UNKNOWN":
            call.hangup(
                "The cancellation could not be verified with the clinic system. "
                "Please call the clinic to check before retrying."
            )
        else:
            call.hangup(
                f"I could not cancel that appointment: {result.message}. "
                "Please call the clinic directly."
            )

    @agent.on_action("cancel_appointment")
    def on_action_cancel(call: guava.Call) -> None:
        if not active(call):
            return
        appointment_id = str(call.get_variable("target_appointment_id") or "").strip()
        result = service.cancel_appointment(
            patient_id=service.bound_patient_id,
            appointment_id=appointment_id,
        )
        if result.completed and result.ehr_status == "OK":
            call.send_instruction(
                "Your appointment is cancelled. Please call the clinic if you need another time."
            )
        elif result.ehr_status == "UNKNOWN":
            call.send_instruction(
                "The cancellation could not be verified. "
                "Please call the clinic to check before retrying."
            )
        else:
            call.send_instruction(f"Cancellation failed: {result.message}")

    @agent.on_escalate
    def on_escalate(call: guava.Call, event: EscalateEvent) -> None:
        if not active(call):
            return
        service.escalate(
            patient_id=service.bound_patient_id,
            reason="Caller requested human clinician or safety threshold triggered",
            requested_by=getattr(event, "requested_by", "agent"),
        )
        call.hangup(
            "I cannot connect a live transfer or confirm a clinical review. "
            "Please call the clinic directly. If this is an emergency, call 911 now."
        )

    @agent.on_session_end
    def on_session_end(call: guava.Call, event: BotSessionEnded) -> None:
        del event
        if service.bind_call(call.id):
            service.end_session()

    return agent
