"""Phase 13 model registry tests.

These tests cover the provider-independent catalog that Phase 13 routing selects
from: registration, lookup, provider profiling, capability selection, local
preference, context-window awareness, health, and unknown-model behavior.
"""

from __future__ import annotations

import pytest

from matlab_os.intelligence.model_registry import (
    ModelProfile,
    ModelRegistry,
    SelectionContext,
    UnknownModelError,
)


def test_register_and_list():
    registry = ModelRegistry()
    registry.register(
        ModelProfile(
            name="local-owl",
            provider_id="ollama",
            locality="local",
            capabilities={"text_generation": True, "local_inference": True},
            context_tokens=4096,
        )
    )
    registry.register(
        ModelProfile(
            name="cloud-gpt",
            provider_id="openai",
            locality="remote",
            capabilities={"text_generation": True, "analysis": True},
            context_tokens=16384,
        )
    )

    models = registry.list_models()
    names = [m["name"] for m in models]
    assert names == ["cloud-gpt", "local-owl"]


def test_get_model():
    registry = ModelRegistry()
    profile = ModelProfile(name="m", provider_id="p", locality="local")
    registry.register(profile)

    found = registry.get_model("m")
    assert found.name == "m"
    assert found.provider_id == "p"
    assert found.locality == "local"
    assert found.to_dict()["notes"] is None


def test_get_model_is_unknown_for_missing():
    registry = ModelRegistry()
    with pytest.raises(UnknownModelError):
        registry.get_model("missing")


def test_profile_exists():
    registry = ModelRegistry()
    assert registry.profile_exists("ollama") is False

    registry.register(ModelProfile(name="m", provider_id="ollama"))
    assert registry.profile_exists("ollama") is True
    assert registry.profile_exists("unknown") is False


def test_select_model_by_capability():
    registry = ModelRegistry()
    registry.register(
        ModelProfile(
            name="a",
            provider_id="p1",
            locality="local",
            capabilities={"text_generation": True},
            context_tokens=1024,
        )
    )
    registry.register(
        ModelProfile(
            name="b",
            provider_id="p2",
            locality="remote",
            capabilities={"text_generation": True, "analysis": True},
            context_tokens=8192,
        )
    )

    chosen = registry.select_model(capabilities=["text_generation"])
    assert chosen is not None
    assert chosen["name"] == "a"


def test_select_model_prefers_local():
    registry = ModelRegistry()
    registry.register(
        ModelProfile(
            name="remote-first",
            provider_id="p1",
            locality="remote",
            capabilities={"text_generation": True},
        )
    )
    registry.register(
        ModelProfile(
            name="local-first",
            provider_id="p2",
            locality="local",
            capabilities={"text_generation": True},
        )
    )

    chosen = registry.select_model(capabilities=["text_generation"], prefer_local=True)
    assert chosen is not None
    assert chosen["name"] == "local-first"


def test_select_model_falls_back_when_no_local():
    registry = ModelRegistry()
    registry.register(
        ModelProfile(
            name="only-remote",
            provider_id="p1",
            locality="remote",
            capabilities={"text_generation": True},
        )
    )

    chosen = registry.select_model(capabilities=["text_generation"], prefer_local=True)
    assert chosen is not None
    assert chosen["name"] == "only-remote"


def test_select_model_respects_context_window():
    registry = ModelRegistry()
    registry.register(
        ModelProfile(
            name="small",
            provider_id="p1",
            locality="local",
            capabilities={"text_generation": True},
            context_tokens=1024,
        )
    )
    registry.register(
        ModelProfile(
            name="large",
            provider_id="p2",
            locality="local",
            capabilities={"text_generation": True},
            context_tokens=16384,
        )
    )

    chosen = registry.select_model(
        capabilities=["text_generation"], required_context_tokens=4096
    )
    assert chosen is not None
    assert chosen["name"] == "large"


def test_select_model_returns_none_when_nothing_matches():
    registry = ModelRegistry()
    registry.register(
        ModelProfile(
            name="a",
            provider_id="p1",
            locality="local",
            capabilities={"text_generation": True},
        )
    )

    chosen = registry.select_model(capabilities=["image_gen"])
    assert chosen is None


def test_select_model_requires_list():
    registry = ModelRegistry()
    with pytest.raises(TypeError):
        registry.select_model(capabilities=("text_generation",))


def test_health_check():
    registry = ModelRegistry()
    assert registry.health_check() == {"models": 0, "providers": 0, "by_locality": {}}

    registry.register(ModelProfile(name="a", provider_id="p1", locality="local"))
    registry.register(ModelProfile(name="b", provider_id="p1", locality="local"))
    registry.register(ModelProfile(name="c", provider_id="p2", locality="remote"))

    health = registry.health_check()
    assert health["models"] == 3
    assert health["providers"] == 2
    assert health["by_locality"] == {"local": 2, "remote": 1}


def test_unregister():
    registry = ModelRegistry()
    registry.register(ModelProfile(name="a", provider_id="p1"))
    registry.register(ModelProfile(name="b", provider_id="p1"))

    assert registry.unregister("a") is True
    assert registry.unregister("a") is False
    assert registry.get_model("b").name == "b"
    assert "a" not in {m["name"] for m in registry.list_models()}


def test_shared_intelligence_delegates_to_registry():
    from matlab_os.intelligence.engine import SharedIntelligence

    registry = ModelRegistry()
    registry.register(
        ModelProfile(
            name="x",
            provider_id="p",
            locality="local",
            capabilities={"text_generation": True},
        )
    )

    si = SharedIntelligence(registry=registry)
    assert si.identify()["sources"] == ["registry"]
    assert si.profile_exists("p") is True
    assert si.model_catalog() == [registry.get_model("x").to_dict()]
    chosen = si.select_model(capabilities=["text_generation"])
    assert chosen is not None
    assert chosen["name"] == "x"
    assert si.health()["registry"]["models"] == 1


def test_shared_intelligence_no_registry_is_no_op():
    from matlab_os.intelligence.engine import SharedIntelligence

    si = SharedIntelligence()
    assert si.identify()["sources"] == []
    assert si.model_catalog() == []
    assert si.select_model(capabilities=["text_generation"]) is None
    assert si.profile_exists("anything") is False
    assert si.health()["registry"]["available"] is False
    assert si.health()["catalog"]["available"] is False


def test_profile_carries_cost_metadata():
    profile = ModelProfile(
        name="p",
        provider_id="p1",
        locality="remote",
        input_cost_per_1k_tokens=0.001,
        output_cost_per_1k_tokens=0.003,
        max_output_tokens=4096,
    )
    d = profile.to_dict()
    assert d["input_cost_per_1k_tokens"] == pytest.approx(0.001)
    assert d["output_cost_per_1k_tokens"] == pytest.approx(0.003)
    assert d["max_output_tokens"] == 4096


def test_estimate_cost_for_completion():
    profile = ModelProfile(
        name="p",
        provider_id="p1",
        locality="remote",
        input_cost_per_1k_tokens=1.0,
        output_cost_per_1k_tokens=2.0,
    )
    cost = profile.estimate_cost_for_completion(
        prompt_tokens=1000, completion_tokens=500
    )
    assert cost == pytest.approx(1.0 * 1.0 + 2.0 * 0.5)


def test_estimate_cost_returns_none_when_no_pricing():
    profile = ModelProfile(name="p", provider_id="p1")
    assert profile.estimate_cost_for_completion(prompt_tokens=1000) is None


def test_selection_with_context():
    registry = ModelRegistry()
    registry.register(
        ModelProfile(
            name="local-small",
            provider_id="p1",
            locality="local",
            capabilities={"text_generation": True},
            context_tokens=2048,
        )
    )
    registry.register(
        ModelProfile(
            name="local-large",
            provider_id="p2",
            locality="local",
            capabilities={"text_generation": True},
            context_tokens=16384,
        )
    )

    ctx = SelectionContext(
        requested_capabilities=["text_generation"],
        required_context_tokens=4096,
        estimated_prompt_tokens=500,
        estimated_completion_tokens=200,
        task_id="t1",
    )
    chosen = registry.select_model(
        capabilities=["text_generation"], context=ctx
    )
    assert chosen is not None
    assert chosen["name"] == "local-large"


def test_selection_context_is_importable():
    from matlab_os.intelligence.model_registry import SelectionContext

    ctx = SelectionContext(
        requested_capabilities=["text_generation"],
        task_id="t",
    )
    assert ctx.requested_capabilities == ["text_generation"]
    assert ctx.task_id == "t"


def test_select_with_fallback_returns_first_success():
    registry = ModelRegistry()
    registry.register(
        ModelProfile(name="a", provider_id="p1", locality="local", capabilities={"text_generation": True})
    )
    registry.register(
        ModelProfile(name="b", provider_id="p2", locality="local", capabilities={"text_generation": True})
    )

    attempts = {"a": False, "b": True}

    def attempt(profile):
        if profile.name == "a":
            raise RuntimeError("first model failed")
        return {"name": profile.name, "status": "ok"}

    result = registry.select_with_fallback(
        capabilities=["text_generation"], attempt=attempt
    )
    assert result == {"name": "b", "status": "ok"}


def test_select_with_fallback_raises_last_failure_when_all_fail():
    registry = ModelRegistry()
    registry.register(
        ModelProfile(name="a", provider_id="p1", locality="local", capabilities={"text_generation": True})
    )
    registry.register(
        ModelProfile(name="b", provider_id="p2", locality="local", capabilities={"text_generation": True})
    )

    def attempt(profile):
        raise RuntimeError(f"{profile.name} failed")

    with pytest.raises(RuntimeError, match="b failed"):
        registry.select_with_fallback(capabilities=["text_generation"], attempt=attempt)


def test_routing_log_and_summary():
    registry = ModelRegistry()
    registry.record_routing(
        task_id="t1",
        requested_capabilities=["text_generation"],
        selected_model="local-llm",
        provider_id="ollama",
        locality="local",
        reason="local preferred",
        estimated_cost_usd=0.0,
    )
    registry.record_routing(
        task_id="t2",
        requested_capabilities=["analysis"],
        selected_model="cloud-gpt",
        provider_id="openai",
        locality="remote",
        reason="capability required",
        estimated_cost_usd=0.001,
    )

    assert registry.routing_summary()["total_decisions"] == 2
    assert registry.routing_summary()["by_locality"] == {"local": 1, "remote": 1}
    assert registry.routing_summary()["by_outcome"] == {"selected": 2}
    assert registry.routing_summary()["estimated_cost_usd"] == pytest.approx(0.001)

    log = registry.routing_log(limit=1)
    assert len(log) == 1
    assert log[0]["task_id"] == "t2"
    assert log[0]["selected_model"] == "cloud-gpt"
