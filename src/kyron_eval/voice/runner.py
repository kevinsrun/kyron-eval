"""Execution harness for Guava Voice AI synthetic agent sessions.

Executes scripted Guava callbacks with synthetic caller text. Private SDK dispatch
and command queues are isolated here as a version-pinned test shim. No Guava
server, model, audio, ASR, PSTN, pickup or spoken response is observed. The selected
EHR may be mock or a real isolated synthetic OpenEMR database.
"""

from typing import Any

import guava
from guava.events import (
    ActionItemCompletedEvent,
    BotSessionEnded,
    CallerSpeechEvent,
    InboundCallEvent,
    TaskCompletedEvent,
)
from guava.types.call_info import PSTNCallInfo

from kyron_eval.evaluate import evaluate
from kyron_eval.models import Result, Scenario
from kyron_eval.replay import MockWorld
from kyron_eval.voice.adapter import GuavaVoiceAdapter
from kyron_eval.voice.agent import build_guava_agent
from kyron_eval.voice.service import KyronVoiceService


def execute_guava_voice(
    scenario: Scenario,
    *,
    adapter=None,
    faults=None,
    caller_dob: str = "1980-01-02",
    caller_phone: str = "2025550100",
    confirm_cancellation: bool = True,
    target_appointment_id: str | None = None,
) -> Result:
    """Offline SDK callback simulation; no audio, telephony, ASR or model executes."""
    explicit_target = target_appointment_id or next(
        (
            turn.facts.get("cancel_appointment_id")
            for turn in scenario.script
            if turn.facts.get("cancel_appointment_id")
        ),
        None,
    )
    if scenario.workflow != "scheduling" or not explicit_target:
        raise ValueError("SDK simulation requires an explicit cancellation scenario/target")
    if adapter is not None and not (
        getattr(adapter, "name", "") == "mock" or getattr(adapter, "synthetic_model_safe", False)
    ):
        raise ValueError("SDK simulation accepts only mock or isolated synthetic OpenEMR")

    # 1. Initialize an explicitly labeled offline SDK simulation.
    world = MockWorld(scenario, "VOICE", adapter=adapter, faults=faults)
    world.trace.evidence_class = "GUAVA_SDK_CALLBACK_SIMULATION"
    world.trace.harness_version = "guava-sdk-simulation-1.1"

    # 2. Build service layer and real Guava agent
    service = KyronVoiceService(world)
    agent = build_guava_agent(service)
    voice_adapter = GuavaVoiceAdapter(world)

    # 3. Create real Guava Call object
    call_info = PSTNCallInfo(
        from_number=f"+1{caller_phone}",
        to_number="+15555550199",
    )
    call = guava.Call("call-synthetic-" + service.session_id, call_info)

    # 4. Inbound call event
    inbound_event = InboundCallEvent(
        caller_number=call_info.from_number,
        agent_number=call_info.to_number,
    )
    voice_adapter.record_inbound_call(inbound_event)

    # 5. Call start: agent sets initial 'identity' task
    if agent._on_call_start:
        agent._on_call_start(call)

    # Drain initial commands from queue
    _drain_commands(call, voice_adapter)

    # 6. Turn 1: Synthetic caller provides identity information
    caller_speech_1 = CallerSpeechEvent(
        utterance=f"Hello, my date of birth is {caller_dob} and my phone number is {caller_phone}.",
        utterance_id="utt-001",
    )
    agent._dispatch_event(call, caller_speech_1)

    # Set field values collected by the task
    field_dob = ActionItemCompletedEvent(key="patient_birth_date", payload=caller_dob)
    field_phone = ActionItemCompletedEvent(key="patient_phone", payload=caller_phone)
    agent._dispatch_event(call, field_dob)
    agent._dispatch_event(call, field_phone)
    voice_adapter.record_field_extracted(field_dob)
    voice_adapter.record_field_extracted(field_phone)

    # Trigger identity task completion
    task_id_event = TaskCompletedEvent(task_id="identity")
    agent._dispatch_event(call, task_id_event)
    voice_adapter.record_task_completed(task_id_event, {"dob": caller_dob, "phone": caller_phone})
    _drain_commands(call, voice_adapter)

    # If identity failed, agent hung up
    if not service.identity_verified:
        agent._dispatch_event(
            call,
            BotSessionEnded(termination_reason="bot-hangup", dnc=False, pickup=True),
        )
        voice_adapter.record_session_ended(
            BotSessionEnded(termination_reason="bot-hangup", dnc=False, pickup=True)
        )
        trace = world.finish()
        metrics = evaluate(scenario, trace, clarity_version="clarity-ehr-3.3")
        return Result(scenario=scenario, trace=trace, metrics=metrics)

    # 7. Turn 2: Synthetic caller requests cancellation
    # Determine target appointment ID
    appt_id = explicit_target

    confirm_str = "yes" if confirm_cancellation else "no"
    confirm_phrase = (
        "Yes, I confirm cancellation." if confirm_cancellation else "Actually, no, keep it."
    )
    caller_speech_2 = CallerSpeechEvent(
        utterance=f"Please cancel appointment {appt_id}. {confirm_phrase}",
        utterance_id="utt-002",
    )
    agent._dispatch_event(call, caller_speech_2)

    field_appt = ActionItemCompletedEvent(key="appointment_id", payload=appt_id)
    field_confirm = ActionItemCompletedEvent(key="caller_confirmation", payload=confirm_str)
    agent._dispatch_event(call, field_appt)
    agent._dispatch_event(call, field_confirm)
    voice_adapter.record_field_extracted(field_appt)
    voice_adapter.record_field_extracted(field_confirm)

    task_cancel_event = TaskCompletedEvent(task_id="cancellation")
    agent._dispatch_event(call, task_cancel_event)
    voice_adapter.record_task_completed(
        task_cancel_event, {"appointment_id": appt_id, "confirmation": confirm_str}
    )
    _drain_commands(call, voice_adapter)

    # 8. Session completion
    end_event = BotSessionEnded(termination_reason="bot-hangup", dnc=False, pickup=True)
    agent._dispatch_event(call, end_event)
    voice_adapter.record_session_ended(end_event)

    # 9. Independent authoritative readback and metrics evaluation
    trace = world.finish()
    metrics = evaluate(scenario, trace, clarity_version="clarity-ehr-3.3")

    return Result(scenario=scenario, trace=trace, metrics=metrics)


def _drain_commands(call: guava.Call, adapter: GuavaVoiceAdapter) -> list[Any]:
    commands = []
    while not call._command_queue.empty():
        cmd = call._command_queue.get_nowait()
        commands.append(cmd)
        cmd_type = getattr(cmd, "command_type", "")
        adapter.record_sdk_command(cmd_type, cmd.model_dump(mode="json"))
    return commands
