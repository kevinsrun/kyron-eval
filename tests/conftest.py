"""Test categories keep real EHR execution explicit; existing tests remain intact."""

import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        name = item.path.name
        if name == "test_fhir.py":
            item.add_marker(pytest.mark.fixture_contract)
        elif name == "test_llm_agent.py":
            item.add_marker(pytest.mark.llm_protocol)
        elif "test_live" not in name:
            item.add_marker(pytest.mark.unit)
