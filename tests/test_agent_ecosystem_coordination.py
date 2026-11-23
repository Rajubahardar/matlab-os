"""Phase 12 multi-agent coordination tests."""
from __future__ import annotations

import pytest

from matlab_os.phase9.collaboration.coordinator import CollaborationCoordinator
from matlab_os.integration.agent_ecosystem import coordination as ec_coordination


class TestCoordination:
    def test_handoff_requires_result(self):
        coordinator = CollaborationCoordinator(config=None, audit=None, directory=None, clock=None)
        coord = ec_coordination.Coordination(coordinator)
        with pytest.raises(Exception):
            coord.handoff("downstream", "upstream")

    def test_provenance(self):
        coordinator = CollaborationCoordinator(config=None, audit=None, directory=None, clock=None)
        coord = ec_coordination.Coordination(coordinator)
        coord.register_result("upstream", {"task_id": "upstream", "agent_id": "phase9-verifier", "status": "completed", "output": {}})
        assert coord.provenance("downstream")["upstream_agents"] == ["upstream"]
