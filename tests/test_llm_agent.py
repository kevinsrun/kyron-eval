"""Protocol/guard tests use scripted synthetic responses, never model performance evidence."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from kyron_eval.ehr.config import create_adapter
from kyron_eval.llm_agent import ChatModelClient, ModelCallError, dispatch, execute_llm
from kyron_eval.replay import MockWorld, load_scenarios
from kyron_eval.storage import Store
from kyron_eval.web import create_app


def scenario(id="booking-confirmed"):
    return next(s for s in load_scenarios() if s.id == id).model_copy(deep=True)


def call(name, **arguments):
    return {
        "id": "test-call",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def response(content=None, tool=None):
    message = {"role": "assistant", "content": content}
    if tool:
        message["tool_calls"] = [tool]
    return {
        "choices": [{"message": message, "finish_reason": "tool_calls" if tool else "stop"}],
        "model": "synthetic-protocol-fixture",
        "id": "synthetic-request",
    }


def test_model_loop_executes_tools_and_receives_actual_results(tmp_path):
    replies = iter(
        [
            response(tool=call("get_availability")),
            response("Please confirm the offered time."),
            response(
                tool=call(
                    "create_appointment",
                    patient_id="synthetic-patient-1",
                    slot_id="synthetic-slot-1",
                )
            ),
            response(tool=call("read_state")),
            response("Your appointment is booked for synthetic-slot-1. You are all set."),
        ]
    )
    requests = []

    def serve(request):
        requests.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer synthetic-test-key"
        return httpx.Response(200, json=next(replies))

    with httpx.Client(transport=httpx.MockTransport(serve)) as http:
        model = ChatModelClient(
            model="synthetic-protocol-fixture", api_key="synthetic-test-key", client=http
        )
        run = Store(tmp_path).execute(["LLM"], ["booking-confirmed"], model_client=model)
    trace = run.results[0].trace
    assert len(trace.state_after.bookings) == 1
    assert any(e.kind == "model" and e.name == "response" for e in trace.events)
    observed = [m for r in requests for m in r["messages"] if m["role"] == "tool"]
    assert any(json.loads(m["content"]).get("state", {}).get("bookings") for m in observed)
    assert not any(
        "synthetic-test-key" in json.dumps(e.model_dump(mode="json")) for e in trace.events
    )
    assert run.configuration["agent_modes"] == ["LLM"]
    assert TestClient(create_app(tmp_path)).get(f"/runs/{run.id}").status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        response(tool={"type": "function", "function": {"name": "read_state", "arguments": "{}"}}),
        response(content={"unexpected": "object"}),
        {"choices": []},
        response(
            tool={
                "id": "x",
                "type": "function",
                "function": {"name": "read_state", "arguments": {}},
            }
        ),
    ],
)
def test_malformed_model_response_is_explicit_failure(payload):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as http:
        model = ChatModelClient(model="test", base_url="http://127.0.0.1:7777/v1", client=http)
        with pytest.raises(ModelCallError) as exc:
            model.complete([], [])
        assert exc.value.code == "MODEL_INVALID_RESPONSE"


def test_model_timeout_records_failure_and_truthful_uncertainty():
    def timeout(request):
        raise httpx.ReadTimeout("synthetic failure")

    with httpx.Client(transport=httpx.MockTransport(timeout)) as http:
        model = ChatModelClient(model="test", base_url="http://127.0.0.1:7777/v1", client=http)
        trace = execute_llm(scenario(), model_client=model)
    assert any(e.name == "MODEL_TIMEOUT" for e in trace.events)
    assert not trace.state_after.bookings
    assert "not verified" in [e.text for e in trace.events if e.kind == "agent"][-1]


@pytest.mark.parametrize(
    "tool,args,code",
    [
        ("execute_shell", "{}", "UNKNOWN_TOOL"),
        ("get_patient", '{"patient_id":null}', "INVALID_MODEL_TOOL_ARGUMENTS"),
        ("get_patient", '{"patient_id":"synthetic-other"}', "PATIENT_MISMATCH"),
        (
            "create_appointment",
            '{"patient_id":"synthetic-patient-1","slot_id":"synthetic-slot-1"}',
            "NO_MATCHING_OFFER",
        ),
        ("read_state", '{"extra":"value"}', "INVALID_MODEL_TOOL_ARGUMENTS"),
    ],
)
def test_model_tool_dispatch_is_allowlisted_and_guarded(tool, args, code):
    world = MockWorld(scenario(), "LLM")
    world.prepare()
    result = dispatch(world, tool, args)
    assert result["failure_code"] == code
    assert not world.state.bookings
    assert any(e.kind == "error" for e in world.trace.events)


def test_model_cannot_invent_routing_facts_or_bypass_triage():
    world = MockWorld(scenario("triage-unclear"), "LLM")
    world.prepare()
    assert (
        dispatch(world, "get_availability", "{}")["failure_code"]
        == "CURRENT_TRIAGE_POLICY_REQUIRED"
    )
    world.facts.update({"right_arm_weakness": "no", "speech_abnormality": "unclear"})
    result = dispatch(
        world, "evaluate_policy", '{"right_arm_weakness":"yes","speech_abnormality":"yes"}'
    )
    assert result["failure_code"] == "FACT_EVIDENCE_MISMATCH"


def test_unknown_route_facts_are_preserved_as_null_and_require_review():
    world = MockWorld(scenario("triage-unclear"), "LLM")
    world.prepare()
    result = dispatch(
        world, "evaluate_policy", '{"right_arm_weakness":null,"speech_abnormality":null}'
    )
    assert result["disposition"] == "HUMAN_REVIEW"


def test_llm_cannot_receive_external_fhir_data(monkeypatch):
    monkeypatch.setenv("KYRON_FHIR_BASE_URL", "http://127.0.0.1:8300/fhir")
    adapter = create_adapter("fhir", scenario())
    try:
        with pytest.raises(ValueError, match="synthetic mock"):
            execute_llm(scenario(), adapter=adapter)
    finally:
        adapter.close()


def test_final_response_at_model_budget_limit_is_not_reported_as_exhaustion():
    s = scenario()
    s.script = s.script[:1]
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json=response("Please call the clinic to arrange an appointment.")
            )
        )
    ) as http:
        model = ChatModelClient(model="test", base_url="http://127.0.0.1:7777/v1", client=http)
        trace = execute_llm(s, model_client=model, max_calls=1)
    assert not any(e.name == "MODEL_BUDGET_EXHAUSTED" for e in trace.events)
