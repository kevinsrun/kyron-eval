"""GuavaVoiceAdapter: Maps official Guava Voice AI SDK events to the Kyron evaluation trace schema.

Captures call lifecycle, speech transcripts, field extraction, tool inputs/outputs,
interruption flags, and escalation/termination without inventing unexposed events.
"""

from typing import Any

from guava.events import (
    ActionItemCompletedEvent,
    AgentSpeechEvent,
    BotSessionEnded,
    CallerSpeechEvent,
    EscalateEvent,
    ExecuteActionEvent,
    InboundCallEvent,
    TaskCompletedEvent,
)

from kyron_eval.replay import MockWorld


class GuavaVoiceAdapter:
    """Binds a Guava Agent and Call session to Kyron's evaluation world and trace recorder."""

    def __init__(self, world: MockWorld):
        self._world = world
        self._raw_events: list[dict[str, Any]] = []

    @property
    def raw_events(self) -> list[dict[str, Any]]:
        return self._raw_events

    def record_inbound_call(self, event: InboundCallEvent) -> None:
        self._raw_events.append(event.model_dump(mode="json"))
        self._world.event(
            "state",
            name="voice.inbound_call",
            caller_number=event.caller_number,
            agent_number=event.agent_number,
        )

    def record_caller_speech(self, event: CallerSpeechEvent) -> None:
        self._raw_events.append(event.model_dump(mode="json"))
        self._world.last_caller = self._world.event(
            "caller",
            text=event.utterance,
            name="voice.caller_speech",
            utterance_id=event.utterance_id,
        )

    def record_agent_speech(self, event: AgentSpeechEvent) -> None:
        self._raw_events.append(event.model_dump(mode="json"))
        self._world.say(event.utterance)
        if event.interrupted:
            self._world.event(
                "state",
                name="voice.agent_interrupted",
                utterance=event.utterance,
            )

    def record_field_extracted(self, event: ActionItemCompletedEvent) -> None:
        self._raw_events.append(event.model_dump(mode="json"))
        self._world.event(
            "state",
            name="voice.field_extracted",
            field_key=event.key,
            field_payload=event.payload,
        )

    def record_task_completed(self, event: TaskCompletedEvent, fields: dict[str, Any]) -> None:
        self._raw_events.append(event.model_dump(mode="json"))
        self._world.event(
            "tool",
            name=f"voice.task_completed.{event.task_id}",
            task_id=event.task_id,
            fields=fields,
            result={"completed": True, "task_id": event.task_id},
        )

    def record_action_executed(self, event: ExecuteActionEvent, outcome: Any = None) -> None:
        self._raw_events.append(event.model_dump(mode="json"))
        self._world.event(
            "tool",
            name=f"voice.action.{event.action_key}",
            action_key=event.action_key,
            result={"executed": True, "outcome": outcome},
        )

    def record_escalation(self, event: EscalateEvent) -> None:
        self._raw_events.append(event.model_dump(mode="json"))
        self._world.event(
            "policy",
            name="voice.escalation_requested",
            requested_by=event.requested_by,
        )

    def record_session_ended(self, event: BotSessionEnded) -> None:
        self._raw_events.append(event.model_dump(mode="json"))
        self._world.event(
            "state",
            name="voice.session_ended",
            termination_reason=event.termination_reason,
            dnc=event.dnc,
            pickup=event.pickup,
        )
        self._world.trace.ehr["guava_voice"] = {
            "evidence_class": "VOICE_LIVE_SYNTHETIC",
            "termination_reason": event.termination_reason,
            "raw_events_count": len(self._raw_events),
            "pickup": event.pickup,
            "dnc": event.dnc,
        }
