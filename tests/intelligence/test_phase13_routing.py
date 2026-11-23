"""Phase 13 routing integration tests.

These tests exercise the unified model-intelligence path against the existing
Phase 9 provider layer via the new provider-backed catalog.  Mock providers are
used because they are deterministic and require no network or Ollama instance.
"""

from __future__ import annotations

import pytest

from matlab_os.intelligence.engine import SharedIntelligence
from matlab_os.intelligence.model_registry.provider_catalog import ProviderCatalog
from matlab_os.intelligence.model_registry.provider_adapter import ProviderModelRegistry
from matlab_os.phase9.providers import MockProvider


def _make_mock(
    *,
    provider_id: str,
    capabilities: list[str],
    locality: str = "local",
    default_model: str = "mock",
):
    config = pytest.importorskip("matlab_os.phase9.config").ProviderConfig(
        provider_id=provider_id,
        enabled=True,
        kind="mock",
        locality=locality,
    )
    provider = MockProvider(config)
    provider.capabilities = frozenset(capabilities)
    provider.default_model = default_model
    provider.locality = locality
    return provider


def test_catalog_register_and_list():
    catalog = ProviderCatalog()
    catalog.register(_make_mock(provider_id="m1", capabilities=["text_generation"]))
    catalog.register(_make_mock(provider_id="m2", capabilities=["text_generation", "analysis"]))

    assert len(catalog.providers) == 2
    assert catalog.providers[0].provider_id == "m1"


def test_catalog_select_capability():
    catalog = ProviderCatalog()
    catalog.register(_make_mock(provider_id="local-tg", capabilities=["text_generation"]))
    catalog.register(_make_mock(provider_id="remote-analysis", capabilities=["analysis"]))

    chosen = catalog.select_provider(capabilities=["analysis"])
    assert chosen is not None
    assert chosen.provider_id == "remote-analysis"


def test_catalog_prefers_local():
    catalog = ProviderCatalog()
    catalog.register(_make_mock(
        provider_id="remote-tg", capabilities=["text_generation"], locality="remote"
    ))
    catalog.register(_make_mock(
        provider_id="local-tg", capabilities=["text_generation"], locality="local"
    ))

    chosen = catalog.select_provider(capabilities=["text_generation"], prefer_local=True)
    assert chosen is not None
    assert chosen.provider_id == "local-tg"


def test_catalog_returns_none_when_nothing_matches():
    catalog = ProviderCatalog()
    catalog.register(_make_mock(provider_id="m1", capabilities=["text_generation"]))

    chosen = catalog.select_provider(capabilities=["image_gen"])
    assert chosen is None


def test_provider_model_registry_profiles_real_providers():
    catalog = ProviderCatalog()
    catalog.register(_make_mock(provider_id="m1", capabilities=["text_generation"], default_model="mock-1"))
    catalog.register(_make_mock(provider_id="m2", capabilities=["analysis"], default_model="mock-2"))

    registry = ProviderModelRegistry(catalog)
    models = registry.list_models()
    names = {m["name"] for m in models}
    assert names == {"mock-1", "mock-2"}


def test_shared_intelligence_routes_through_catalog():
    catalog = ProviderCatalog()
    catalog.register(_make_mock(provider_id="local-llm", capabilities=["text_generation"]))
    catalog.register(_make_mock(provider_id="cloud-llm", capabilities=["analysis"]))

    si = SharedIntelligence(catalog=catalog)
    assert si.identify()["sources"] == ["catalog"]

    chosen = si.select_model(capabilities=["analysis"])
    assert chosen is not None
    assert chosen["source"] == "catalog"
    assert chosen["provider_id"] == "cloud-llm"


def test_shared_intelligence_selects_provider_object():
    catalog = ProviderCatalog()
    catalog.register(_make_mock(provider_id="local-llm", capabilities=["text_generation"]))

    si = SharedIntelligence(catalog=catalog)
    provider = si.select_provider(capabilities=["text_generation"])
    assert provider is not None
    assert provider.provider_id == "local-llm"
    assert provider.is_available() is True


def test_shared_intelligence_health_observability():
    catalog = ProviderCatalog()
    catalog.register(_make_mock(provider_id="m1", capabilities=["text_generation"]))

    si = SharedIntelligence(catalog=catalog)
    health = si.health()
    assert health["layer"] == "universal_model_intelligence"
    assert health["catalog"]["available"] is True
    assert health["catalog"]["providers"] == 1


def test_shared_intelligence_no_source_is_no_op():
    si = SharedIntelligence()
    assert si.identify()["sources"] == []
    assert si.model_catalog() == []
    assert si.select_model(capabilities=["text_generation"]) is None
    assert si.select_provider(capabilities=["text_generation"]) is None
    assert si.profile_exists("anything") is False
    assert si.health()["registry"]["available"] is False
    assert si.health()["catalog"]["available"] is False


def test_shared_intelligence_prefers_catalog_over_registry():
    from matlab_os.intelligence.model_registry import ModelRegistry

    class DummyRegistry:
        def list_models(self):
            return [{"name": "dummy"}]

        def select_model(self, **kwargs):
            return {"name": "dummy", "source": "registry"}

    catalog = ProviderCatalog()
    catalog.register(_make_mock(provider_id="real", capabilities=["text_generation"]))

    si = SharedIntelligence(catalog=catalog, registry=DummyRegistry())
    chosen = si.select_model(capabilities=["text_generation"])
    assert chosen is not None
    assert chosen["source"] == "catalog"
    assert chosen["provider_id"] == "real"


def test_facade_select_model_accepts_context():
    catalog = ProviderCatalog()
    catalog.register(
        _make_mock(
            provider_id="remote-deep",
            capabilities=["text_generation", "reasoning"],
            locality="remote",
        )
    )

    si = SharedIntelligence(catalog=catalog)
    chosen = si.select_model(
        capabilities=["reasoning"],
        context={
            "requested_capabilities": ["reasoning"],
            "prefer_local": False,
            "estimated_prompt_tokens": 1000,
            "task_id": "r1",
        },
    )
    assert chosen is not None
    assert chosen["provider_id"] == "remote-deep"


def test_facade_fallback_tries_next_provider_on_failure():
    from matlab_os.phase9.providers.base import ProviderRequest

    first = _make_mock(
        provider_id="first", capabilities=["text_generation"], default_model="m1"
    )
    second = _make_mock(
        provider_id="second", capabilities=["text_generation"], default_model="m2"
    )

    catalog = ProviderCatalog()
    catalog.register(first)
    catalog.register(second)

    first.script_error(RuntimeError("first provider failed"))

    si = SharedIntelligence(catalog=catalog)

    def attempt(provider):
        return provider.generate(
            ProviderRequest(prompt="hi", model=provider.default_model or "")
        )

    result = si.select_model_with_fallback(
        capabilities=["text_generation"], attempt=attempt
    )
    assert result.provider_id == "second"
    assert result.model == "m2"


def test_facade_routing_summary_is_observable():
    from matlab_os.intelligence.model_registry.provider_adapter import ProviderModelRegistry

    catalog = ProviderCatalog()
    catalog.register(_make_mock(provider_id="m", capabilities=["text_generation"]))
    registry = ProviderModelRegistry(catalog)

    registry.record_routing(
        task_id="t1",
        requested_capabilities=["text_generation"],
        selected_model="mock",
        provider_id="m",
        locality="local",
        reason="available",
    )

    si = SharedIntelligence(catalog=catalog, registry=registry)
    summary = si.routing_summary()
    assert summary["total_decisions"] == 1
    assert summary["by_locality"] == {"local": 1}
