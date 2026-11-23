"""Phase 12 capability-based selection tests."""
from __future__ import annotations

import pytest

from matlab_os.phase9.agents.capability import AgentCapability, AgentProfile
from matlab_os.phase9.agents.directory import AgentDirectory
from matlab_os.phase9.agents.selector import AgentSelector
from matlab_os.integration.agent_ecosystem import selection as ec_selection


class TestSelection:
    def test_eligible_agent_selected(self):
        directory = AgentDirectory()
        directory.register(AgentProfile(
            agent_id="matlab-admin",
            display_name="MATLAB Admin",
            capabilities={"read": AgentCapability(name="read", tools=("client",), max_complexity=2)},
            tools=frozenset({"client"}),
            permission_scope=frozenset({"client.read"}),
            max_complexity=2,
            max_concurrent_tasks=1,
            locality="local",
        ))
        selector = ec_selection.SelectionAdapter(AgentSelector(directory))
        result = selector.select(
            agent_ids=["matlab-admin"],
            required_capabilities=["read"],
            required_tools=["client"],
        )
        assert result["eligible"].agent_id == "matlab-admin"

    def test_no_capability_returns_failure(self):
        directory = AgentDirectory()
        directory.register(AgentProfile(
            agent_id="matlab-admin",
            display_name="MATLAB Admin",
            capabilities={"write": AgentCapability(name="write", tools=("client",), max_complexity=2)},
            tools=frozenset({"client"}),
            permission_scope=frozenset({"client.write"}),
            max_complexity=2,
            max_concurrent_tasks=1,
            locality="local",
        ))
        selector = ec_selection.SelectionAdapter(AgentSelector(directory))
        with pytest.raises(Exception):
            selector.select(
                agent_ids=["matlab-admin"],
                required_capabilities=["read"],
            )
