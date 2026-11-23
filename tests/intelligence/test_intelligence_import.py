"""Verify that ``matlab_os.intelligence`` imports cleanly and exposes the facade.

This test exists to confirm the immediate blocker is resolved: the package must
import without ``ModuleNotFoundError``, and the public ``SharedIntelligence``
symbol must exist with the Phase 13 universal model-intelligence surface.

These tests are intentionally minimal.  Full routing/registry behavior is tested
in later Phase 13 slices and reuse existing provider/registry surfaces when those
are present on the branch.
"""

from __future__ import annotations

import pytest

import matlab_os.intelligence
from matlab_os.intelligence.engine import SharedIntelligence


def test_intelligence_package_imports_cleanly():
    """The package must import without raising ModuleNotFoundError."""
    assert hasattr(matlab_os.intelligence, "SharedIntelligence")
    assert hasattr(matlab_os.intelligence, "model_provider")


def test_shared_intelligence_is_the_facade():
    """SharedIntelligence is the universal model-intelligence facade, not a skill-engine."""
    si = SharedIntelligence()
    identified = si.identify()
    assert identified["layer"] == "universal_model_intelligence"
    assert identified["phase"] == 13
    assert identified["sources"] == []


def test_facade_rejects_selection_without_registry():
    """Selection without a registry is a no-op, not a crash."""
    si = SharedIntelligence()
    assert si.select_model(capabilities=["text_generation"]) is None
    assert si.model_catalog() == []
    assert si.profile_exists("anything") is False


def test_facade_health_is_observable():
    si = SharedIntelligence()
    health = si.health()
    assert health["layer"] == "universal_model_intelligence"
    assert health["registry"]["available"] is False
    assert health["catalog"]["available"] is False


def test_model_provider_is_reachable_from_package():
    """The present model_provider module should be reachable from the package."""
    mp = matlab_os.intelligence.model_provider
    assert hasattr(mp, "ModelProvider")
    assert hasattr(mp, "OllamaProvider")
    assert hasattr(mp, "CloudLLMProvider")
    assert hasattr(mp, "LocalModelProvider")


@pytest.mark.parametrize("name", ["SharedIntelligence", "model_provider"])
def test_public_exports(name):
    """The package's __all__ should list the symbols it advertises."""
    assert name in matlab_os.intelligence.__all__
