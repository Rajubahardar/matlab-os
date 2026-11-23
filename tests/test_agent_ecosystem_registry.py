"""Phase 12 agent registry tests."""
from __future__ import annotations

import pytest

from matlab_os.phase9.agents.capability import AgentCapability, AgentProfile
from matlab_os.integration.agent_ecosystem import registry as ec_registry

from matlab_os.phase9.agents.capability import AgentStatus


class _Profile:
    def __init__(self, agent_id, enabled=True):
        self._profile = AgentProfile(
            agent_id=agent_id,
            display_name=agent_id,
            capabilities={
                "read": AgentCapability(name="read", tools=("client",), max_complexity=2),
                "write": AgentCapability(name="write", tools=("client",), max_complexity=3),
            },
            tools=frozenset({"client", "workspace"}),
            permission_scope=frozenset({"client.read", "client.write"}),
            max_complexity=3,
            max_concurrent_tasks=1,
            locality="local",
            status="available",
            enabled=enabled,
        )

    @property
    def profile(self):
        return self._profile


class TestRegistryRegistration:
    def test_register_and_discover(self):
        reg = ec_registry.Registry()
        reg.register(_Profile("matlab-admin").profile)
        assert any(row["agent_id"] == "matlab-admin" for row in reg.all())

    def test_duplicate_rejected(self):
        reg = ec_registry.Registry()
        p = _Profile("matlab-admin")
        reg.register(p.profile)
        with pytest.raises(ec_registry.DuplicateRegistrationError):
            reg.register(p.profile)

    def test_unknown_agent_raises(self):
        reg = ec_registry.Registry()
        with pytest.raises(KeyError):
            reg.get("missing")


class TestRegistryLifecycle:
    def test_initialize(self):
        reg = ec_registry.Registry()
        p = _Profile("matlab-admin")
        reg.register(p.profile)
        reg.initialize(p.profile.agent_id)
        # The registry only tracks lifecycle state in its own store; the
        # AgentProfile returned by get() may still carry the agent-level
        # status from registration. Assert the registry-level state instead.
        assert reg._states[p.profile.agent_id] == 'initialized'

    def test_failures_are_terminal(self):
        reg = ec_registry.Registry()
        p = _Profile("matlab-admin")
        reg.register(p.profile)
        reg.update_status(p.profile.agent_id, "failed")
        with pytest.raises(Exception):
            reg.initialize(p.profile.agent_id)
