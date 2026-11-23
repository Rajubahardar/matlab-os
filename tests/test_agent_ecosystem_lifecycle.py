"""Phase 12 agent lifecycle management tests."""
from __future__ import annotations

import pytest

from matlab_os.phase9.agents.capability import AgentCapability, AgentProfile, AgentStatus
from matlab_os.integration.agent_ecosystem import contract as ec
from matlab_os.integration.agent_ecosystem import lifecycle as ec_lifecycle


class _FakeRegistry:
    def __init__(self):
        self._states = {}
        self.audit_log = []

    def register(self, obj):
        self._states[obj.agent_id] = AgentStatus.AVAILABLE

    def _coerce(self, value):
        if isinstance(value, AgentStatus):
            return value
        lowered = value.strip().lower()
        for member in AgentStatus:
            if member.value == lowered:
                return member
        raise ValueError(f'Unknown agent status: {value!r}')

    def get(self, agent_id):
        if agent_id not in self._states:
            raise KeyError(agent_id)
        return AgentProfile(
            agent_id=agent_id,
            display_name=agent_id,
            capabilities={},
            tools=(),
            permission_scope=(),
            max_complexity=1,
            max_concurrent_tasks=1,
            locality="local",
            status=AgentStatus.AVAILABLE,
            enabled=True,
        )

    def update_status(self, agent_id, status):
        self._states[agent_id] = status

    def __contains__(self, item):
        return item in self._states


class TestLifecycle:
    def test_full_lifecycle(self):
        reg = _FakeRegistry()
        agent = AgentProfile(
            agent_id="matlab-admin",
            display_name="MATLAB Admin",
            capabilities={},
            tools=(),
            permission_scope=(),
            max_complexity=1,
            max_concurrent_tasks=1,
            locality="local",
            status=AgentStatus.AVAILABLE,
            enabled=True,
        )
        reg.register(agent)
        # The lifecycle manager's state checks use the registry-level lifecycle
        # state (which the fake registry stores in _states). Keep that in the
        # ecosystem lifecycle shape; the agent profile status can stay available.
        reg._states["matlab-admin"] = "registered"
        lm = ec_lifecycle.LifecycleManager(reg)
        lm.initialize("matlab-admin")
        lm.ready("matlab-admin")
        lm.execute("matlab-admin", ec.TaskContract(
            task_id="t1",
            objective="hello",
            inputs={},
        ))
        lm.stop("matlab-admin")
        # After stop(), the agent should be in a terminal state and further
        # lifecycle transitions that require non-terminal state should fail.
        with pytest.raises(Exception):
            lm.ready("matlab-admin")
