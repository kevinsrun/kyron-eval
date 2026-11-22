"""Optional bounded OpenAI-compatible text tool loop; deterministic mode needs no key.

No provider SDK, live voice, general framework, or implicit model invocation. This mode
accepts curated mock or isolated marker-checked OpenEMR data; generic FHIR stays blocked.
"""

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from kyron_eval.replay import MockWorld

PROMPT_VERSION = "synthetic-tool-agent-1.0"


class ModelCallError(Exception):
    def __init__(self, code):
        self.code = code


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str | None = None
    slot_id: str | None = None
    appointment_id: str | None = None
    right_arm_weakness: str | None = None
    speech_abnormality: str | None = None
    reason_code: str | None = None
    birth_date: str | None = None
    phone: str | None = None


TOOL_FIELDS = {
    "get_patient": ["patient_id"],
    "verify_identity": ["patient_id", "birth_date", "phone"],
    "get_appointments": ["patient_id"],
    "get_availability": [],
    "create_appointment": ["patient_id", "slot_id"],
    "cancel_appointment": ["patient_id", "appointment_id"],
    "get_clinical_context": ["patient_id"],
    "create_human_review_request": ["reason_code"],
    "evaluate_policy": ["right_arm_weakness", "speech_abnormality"],
    "read_state": [],
}
DESCRIPTIONS = {
    "get_availability": (
        "Search fresh availability and return a ranked, expiring appointment offer."
    ),
    "create_appointment": (
        "Attempt a booking through confirmation and policy guards; verify by read_state."
    ),
    "evaluate_policy": (
        "Evaluate the versioned synthetic example routing rule using caller evidence."
    ),
    "read_state": "Read authoritative EHR state; a failed read makes the outcome unknown.",
    "cancel_appointment": (
        "Cancel only after explicit caller confirmation bound to the appointment ID."
    ),
}


def tool_definitions():
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": DESCRIPTIONS.get(name, name.replace("_", " ")),
                "parameters": {
                    "type": "object",
                    "properties": {
                        key: {"type": ["string", "null"] if name == "evaluate_policy" else "string"}
                        for key in keys
                    },
                    "required": keys,
                    "additionalProperties": False,
                },
            },
        }
        for name, keys in TOOL_FIELDS.items()
    ]


class ChatModelClient:
    def __init__(self, *, model=None, base_url=None, api_key=None, client=None):
        self.model = model or os.environ.get("KYRON_LLM_MODEL")
        self.base_url = (
            base_url or os.environ.get("KYRON_LLM_BASE_URL", "https://api.openai.com/v1")
        ).rstrip("/")
        self._key = api_key or os.environ.get("KYRON_LLM_API_KEY")
        parsed = urlsplit(self.base_url)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        allowed = set(os.environ.get("KYRON_LLM_ALLOWED_HOSTS", "api.openai.com").split(","))
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Model URL cannot contain credentials, query or fragment")
        if not local and (parsed.scheme != "https" or parsed.hostname not in allowed):
            raise ValueError("Model endpoint must be local or an explicitly allowed HTTPS host")
        if not self.model or (not local and not self._key):
            raise ValueError(
                "Optional LLM mode requires KYRON_LLM_MODEL and a key for remote providers"
            )
        self._client = client or httpx.Client(timeout=15, follow_redirects=False, trust_env=False)
        self._owns_client = client is None

    def public_config(self):
        return {
            "model": self.model,
            "endpoint_host": urlsplit(self.base_url).hostname,
            "engine": "openai-compatible-http",
            "prompt_version": PROMPT_VERSION,
            "synthetic_mock_only": False,
            "data_boundary": "mock or isolated manifest-verified OpenEMR; generic FHIR blocked",
        }

    def complete(self, messages, tools):
        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "max_completion_tokens": 700,
        }
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers=headers,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise ModelCallError("MODEL_TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise ModelCallError("MODEL_CONNECTION_FAILED") from exc
        if not response.is_success:
            raise ModelCallError(f"MODEL_HTTP_{response.status_code}")
        try:
            payload = response.json()
            message = payload["choices"][0]["message"]
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError("Unexpected role")
            validate_message(message)
            safe = {
                "role": "assistant",
                "content": message.get("content"),
                **({"tool_calls": message["tool_calls"]} if message.get("tool_calls") else {}),
            }
            return {
                "message": safe,
                "model": payload.get("model", self.model),
                "request_id": payload.get("id"),
                "usage": payload.get("usage", {}),
                "finish_reason": payload["choices"][0].get("finish_reason"),
            }
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelCallError("MODEL_INVALID_RESPONSE") from exc

    def close(self):
        if self._owns_client:
            self._client.close()


def model_status():
    try:
        model = ChatModelClient()
        config = model.public_config()
        model.close()
        return {"configured": True, **config}
    except ValueError as exc:
        return {
            "configured": False,
            "reason": str(exc),
            "synthetic_mock_only": False,
            "data_boundary": "mock or isolated manifest-verified OpenEMR; generic FHIR blocked",
        }


def validate_message(message):
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ValueError("Invalid assistant message")
    if message.get("content") is not None and not isinstance(message["content"], str):
        raise ValueError("Invalid message content")
    calls = message.get("tool_calls", [])
    if not isinstance(calls, list) or len(calls) > 4:
        raise ValueError("Invalid tool calls")
    ids = set()
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function":
            raise ValueError("Invalid tool call")
        function = call.get("function", {})
        if (
            not isinstance(call.get("id"), str)
            or not call["id"]
            or call["id"] in ids
            or not isinstance(function, dict)
            or not isinstance(function.get("name"), str)
            or not isinstance(function.get("arguments"), str)
        ):
            raise ValueError("Invalid tool call fields")
        ids.add(call["id"])


def dispatch(world, name, raw_arguments):
    if name not in TOOL_FIELDS:
        world.event("error", name="UNKNOWN_MODEL_TOOL", origin="agent_error", requested_tool=name)
        return {"completed": False, "failure_code": "UNKNOWN_TOOL"}
    try:
        args = json.loads(raw_arguments)
        if set(args) != set(TOOL_FIELDS[name]):
            raise ValueError("Tool argument fields do not match the declared tool")
        data = ToolArguments.model_validate(args).model_dump(include=set(TOOL_FIELDS[name]))
        if name != "evaluate_policy" and any(not value for value in data.values()):
            raise ValueError("Required tool values must be nonempty strings")
    except (ValueError, TypeError, ValidationError):
        world.event("error", name="INVALID_MODEL_TOOL_ARGUMENTS", origin="agent_error", tool=name)
        return {"completed": False, "failure_code": "INVALID_MODEL_TOOL_ARGUMENTS"}
    if "patient_id" in data and data["patient_id"] != world.state.patient_id:
        world.event("error", name="PATIENT_MISMATCH", origin="policy_rejection", tool=name)
        return {"completed": False, "failure_code": "PATIENT_MISMATCH"}
    public = {
        "get_availability": "search",
        "create_appointment": "book",
        "cancel_appointment": "cancel",
        "evaluate_policy": "route",
        "create_human_review_request": "request_review",
    }.get(name, name)
    return world.tool(public, **data)


def execute_llm(scenario, *, adapter=None, faults=None, model_client=None, max_calls=12):
    if (
        adapter is not None
        and adapter.name != "mock"
        and not (adapter.name == "openemr" and getattr(adapter, "synthetic_model_safe", False))
    ):
        raise ValueError(
            "LLM mode accepts synthetic mock or isolated marker-checked "
            "OpenEMR data; generic FHIR is blocked"
        )
    model = model_client or ChatModelClient()
    owned = model_client is None
    world = MockWorld(scenario, "LLM", adapter=adapter, faults=faults)
    world.workflow = scenario.workflow
    world.prepare()
    prompt = (Path(__file__).parent / "data/agent_prompt.txt").read_text()
    context = {
        "workflow": scenario.workflow,
        "patient_id": world.state.patient_id,
        "capabilities": world.adapter.capabilities(),
        "example_policy": {
            "policy_id": scenario.policy_id,
            "version": scenario.policy_version,
            "emergency_all_of": {"right_arm_weakness": "yes", "speech_abnormality": "yes"},
            "otherwise": "HUMAN_REVIEW",
        }
        if scenario.workflow == "triage"
        else {
            "policy_id": scenario.policy_id,
            "version": scenario.policy_version,
            "explicit_confirmation_required": True,
        },
        "identity_fixture_verified": world.identity_verified,
    }
    messages = [{"role": "system", "content": prompt + "\n" + json.dumps(context)}]
    tools = tool_definitions()
    calls = 0
    world.trace.ehr["model_configuration"] = {
        **model.public_config(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "maximum_model_calls": max_calls,
    }
    try:
        for turn in scenario.script:
            world.now += timedelta(seconds=turn.elapsed_seconds)
            world.last_caller = world.event(
                "caller",
                text=turn.text,
                facts=turn.facts,
                confirms_offer=turn.confirms_offer,
                confirms_cancellation=getattr(turn, "confirms_cancellation", False),
            )
            world.facts.update(turn.facts)
            messages.append({"role": "user", "content": turn.text})
            while calls < max_calls:
                calls += 1
                world.event(
                    "model",
                    name="request",
                    request={
                        "messages": json.loads(json.dumps(messages)),
                        "tools": tools,
                        "configuration": model.public_config(),
                    },
                )
                response = model.complete(messages, tools)
                world.event("model", name="response", response=response)
                message = response["message"]
                try:
                    validate_message(message)
                except (ValueError, TypeError) as exc:
                    raise ModelCallError("MODEL_INVALID_RESPONSE") from exc
                messages.append(message)
                if message.get("content"):
                    world.say(message["content"])
                tool_calls = message.get("tool_calls", [])
                if not tool_calls:
                    break
                for call in tool_calls:
                    function = call.get("function", {})
                    result = dispatch(
                        world, function.get("name", ""), function.get("arguments", "{}")
                    )
                    messages.append(
                        {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)}
                    )
            else:
                world.event("error", name="MODEL_BUDGET_EXHAUSTED", origin="agent_execution")
                world.say(
                    "I cannot verify a final resolution within this session. Please "
                    "call the clinic to check the status."
                )
                break
    except ModelCallError as exc:
        world.event("error", name=exc.code, origin="model_failure")
        world.say(
            "The assistant service is unavailable. The outcome is not "
            "verified. Please call the clinic to check before retrying."
        )
    finally:
        if owned:
            model.close()
    return world.finish()
