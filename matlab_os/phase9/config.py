"""Phase 9 configuration.

Design rules
------------
* No credential is ever stored in a configuration object.  A provider record
  carries only the *name* of the environment variable that holds the secret.
* DeepSeek is **disabled by default**.  Enabling it requires an explicit
  ``enabled = true`` in configuration; the provider then also requires the
  credential environment variable to be present at call time.
* Every default is tuned for the documented development host
  (Intel i9, 16 GB RAM, Intel Iris Xe integrated graphics, ~512 GB SSD,
  no dedicated NVIDIA GPU).
* Secrets are read from the process environment only, on demand, and are
  never included in ``redacted_snapshot()``.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping, Optional

from .errors import ConfigurationError

SECTION_NAMES = (
    "providers",
    "resources",
    "security",
    "planning",
    "collaboration",
)


# --------------------------------------------------------------------------- #
# Provider configuration
# --------------------------------------------------------------------------- #
@dataclass
class ProviderConfig:
    """Configuration for one model provider.

    ``api_key_env`` names an environment variable.  It is *not* the secret.
    """

    provider_id: str
    kind: str
    enabled: bool = False
    endpoint: str = ""
    model: str = ""
    timeout_seconds: float = 60.0
    connect_timeout_seconds: float = 10.0
    max_retries: int = 1
    retry_backoff_seconds: float = 1.0
    stream: bool = False
    api_key_env: Optional[str] = None
    api_key_required: bool = True
    tls_verify: bool = True
    locality: str = "local"
    max_tokens: int = 1024
    temperature: float = 0.2
    capabilities: tuple[str, ...] = ()

    def validate(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ConfigurationError("provider_id must be a non-empty string.")
        if self.kind not in {"deepseek", "ollama", "mock"}:
            raise ConfigurationError(
                f"Unsupported provider kind: {self.kind!r}",
                provider_id=self.provider_id,
            )
        if self.timeout_seconds <= 0:
            raise ConfigurationError(
                "timeout_seconds must be positive.",
                provider_id=self.provider_id,
            )
        if self.connect_timeout_seconds <= 0:
            raise ConfigurationError(
                "connect_timeout_seconds must be positive.",
                provider_id=self.provider_id,
            )
        if self.timeout_seconds < self.connect_timeout_seconds:
            raise ConfigurationError(
                "timeout_seconds cannot be smaller than connect_timeout_seconds.",
                provider_id=self.provider_id,
            )
        if self.max_retries < 0:
            raise ConfigurationError(
                "max_retries cannot be negative.",
                provider_id=self.provider_id,
            )
        if self.max_tokens < 1:
            raise ConfigurationError(
                "max_tokens must be positive.",
                provider_id=self.provider_id,
            )
        if not 0.0 <= self.temperature <= 2.0:
            raise ConfigurationError(
                "temperature must be within [0, 2].",
                provider_id=self.provider_id,
            )
        if self.kind in {"deepseek"}:
            if not self.endpoint.startswith(("https://", "http://")):
                raise ConfigurationError(
                    "Remote providers require an http(s) endpoint.",
                    provider_id=self.provider_id,
                )
            if self.enabled and self.api_key_required:
                if not self.api_key_env:
                    raise ConfigurationError(
                        "An enabled credential provider must name api_key_env.",
                        provider_id=self.provider_id,
                    )
                if not _is_env_identifier(self.api_key_env):
                    raise ConfigurationError(
                        "api_key_env must be a valid environment variable name.",
                        provider_id=self.provider_id,
                    )
        if self.locality not in {"local", "remote"}:
            raise ConfigurationError(
                "locality must be 'local' or 'remote'.",
                provider_id=self.provider_id,
            )

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("api_key_env", None)
        data["capabilities"] = list(self.capabilities)
        data["credential_configured"] = self.has_credential()
        return data

    def has_credential(self) -> bool:
        """Return True when the named environment variable carries a value.

        Never returns or logs the value itself.
        """
        if not self.api_key_env:
            return False
        return bool(os.environ.get(self.api_key_env))

    def resolve_credential(self) -> Optional[str]:
        """Read the credential from the environment on demand.

        The caller owns the returned value and must not persist it.  Returns
        ``None`` when unset.
        """
        if not self.api_key_env:
            return None
        value = os.environ.get(self.api_key_env)
        return value if value else None


def _is_env_identifier(name: str) -> bool:
    if not name or not isinstance(name, str):
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(char.isalnum() or char == "_" for char in name)


# --------------------------------------------------------------------------- #
# Resource configuration
# --------------------------------------------------------------------------- #
@dataclass
class ResourceConfig:
    """Execution ceilings for a 16 GB / integrated-graphics host."""

    max_concurrent_agents: int = 2
    max_concurrent_model_calls: int = 1
    max_concurrent_subprocesses: int = 2
    max_cpu_load_percent: float = 85.0
    min_free_ram_gb: float = 1.0
    max_process_ram_gb: float = 6.0
    telemetry_sample_seconds: float = 1.0
    telemetry_max_age_seconds: float = 15.0
    thermal_limit_c: Optional[float] = None
    fail_closed_without_telemetry: bool = True

    def validate(self) -> None:
        for name in (
            "max_concurrent_agents",
            "max_concurrent_model_calls",
            "max_concurrent_subprocesses",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ConfigurationError(f"{name} must be an integer >= 1.")
        if not 0 < self.max_cpu_load_percent <= 100:
            raise ConfigurationError(
                "max_cpu_load_percent must be within (0, 100]."
            )
        if self.min_free_ram_gb < 0:
            raise ConfigurationError("min_free_ram_gb cannot be negative.")
        if self.max_process_ram_gb <= 0:
            raise ConfigurationError("max_process_ram_gb must be positive.")
        if self.telemetry_sample_seconds <= 0:
            raise ConfigurationError("telemetry_sample_seconds must be positive.")
        if self.telemetry_max_age_seconds <= 0:
            raise ConfigurationError(
                "telemetry_max_age_seconds must be positive."
            )
        if self.thermal_limit_c is not None and self.thermal_limit_c <= 0:
            raise ConfigurationError("thermal_limit_c must be positive.")


# --------------------------------------------------------------------------- #
# Security configuration
# --------------------------------------------------------------------------- #
@dataclass
class SecurityConfig:
    """Workspace and approval policy."""

    workspace_root: str = ""
    require_approval_for_writes: bool = True
    require_approval_for_deletion: bool = True
    require_approval_for_provider_upgrade: bool = True
    allow_deletions: bool = False
    allow_symlinks: bool = False
    allow_reparse_points: bool = False
    allow_absolute_operation_paths: bool = False
    max_file_bytes: int = 2_000_000
    max_operations_per_task: int = 200
    max_path_components: int = 32
    backups_enabled: bool = True
    backup_retention: int = 50
    audit_enabled: bool = True
    fail_closed: bool = True

    def validate(self) -> None:
        if not self.workspace_root:
            raise ConfigurationError(
                "security.workspace_root must be configured explicitly."
            )
        if self.max_file_bytes < 1:
            raise ConfigurationError("max_file_bytes must be positive.")
        if self.max_operations_per_task < 1:
            raise ConfigurationError(
                "max_operations_per_task must be positive."
            )
        if self.max_path_components < 1:
            raise ConfigurationError("max_path_components must be positive.")
        if self.backup_retention < 0:
            raise ConfigurationError("backup_retention cannot be negative.")


# --------------------------------------------------------------------------- #
# Planning configuration
# --------------------------------------------------------------------------- #
@dataclass
class PlanningConfig:
    """Bounded planning and re-planning budgets."""

    max_steps: int = 24
    max_subtask_depth: int = 2
    max_parallel_steps: int = 1
    replan_limit: int = 2
    step_timeout_seconds: float = 120.0
    plan_timeout_seconds: float = 300.0
    use_model_assistance: bool = True
    require_dependency_acyclicity: bool = True

    def validate(self) -> None:
        if not 1 <= self.max_steps <= 200:
            raise ConfigurationError("max_steps must be within [1, 200].")
        if not 0 <= self.max_subtask_depth <= 5:
            raise ConfigurationError("max_subtask_depth must be within [0, 5].")
        if self.max_parallel_steps < 1:
            raise ConfigurationError("max_parallel_steps must be >= 1.")
        if not 0 <= self.replan_limit <= 10:
            raise ConfigurationError("replan_limit must be within [0, 10].")
        if self.step_timeout_seconds <= 0:
            raise ConfigurationError("step_timeout_seconds must be positive.")
        if self.plan_timeout_seconds <= 0:
            raise ConfigurationError("plan_timeout_seconds must be positive.")


# --------------------------------------------------------------------------- #
# Collaboration configuration
# --------------------------------------------------------------------------- #
@dataclass
class CollaborationConfig:
    """Budgets that prevent uncontrolled recursive delegation."""

    max_delegation_depth: int = 2
    max_agents_per_task: int = 4
    max_total_delegations: int = 12
    max_messages: int = 64
    max_payload_bytes: int = 64_000
    message_timeout_seconds: float = 60.0
    detect_conflicts: bool = True

    def validate(self) -> None:
        if not 0 <= self.max_delegation_depth <= 5:
            raise ConfigurationError(
                "max_delegation_depth must be within [0, 5]."
            )
        if self.max_agents_per_task < 1:
            raise ConfigurationError("max_agents_per_task must be >= 1.")
        if self.max_total_delegations < 1:
            raise ConfigurationError("max_total_delegations must be >= 1.")
        if self.max_messages < 1:
            raise ConfigurationError("max_messages must be >= 1.")
        if self.max_payload_bytes < 256:
            raise ConfigurationError("max_payload_bytes must be >= 256.")
        if self.message_timeout_seconds <= 0:
            raise ConfigurationError(
                "message_timeout_seconds must be positive."
            )


# --------------------------------------------------------------------------- #
# Aggregate configuration
# --------------------------------------------------------------------------- #
@dataclass
class Phase9Config:
    """Top-level Phase 9 configuration object."""

    version: int = 1
    profile_name: str = "staging"
    providers: list[ProviderConfig] = field(default_factory=list)
    resources: ResourceConfig = field(default_factory=ResourceConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    planning: PlanningConfig = field(default_factory=PlanningConfig)
    collaboration: CollaborationConfig = field(
        default_factory=CollaborationConfig
    )

    # -- construction ------------------------------------------------------ #
    @classmethod
    def default(cls, workspace_root: str) -> "Phase9Config":
        """Return the safe staging default for *workspace_root*.

        DeepSeek is present but explicitly disabled.
        """
        config = cls(
            providers=[
                ProviderConfig(
                    provider_id="deepseek",
                    kind="deepseek",
                    enabled=False,
                    endpoint="https://api.deepseek.com",
                    model="deepseek-chat",
                    timeout_seconds=60.0,
                    connect_timeout_seconds=10.0,
                    max_retries=1,
                    retry_backoff_seconds=1.0,
                    stream=False,
                    api_key_env="DEEPSEEK_API_KEY",
                    api_key_required=True,
                    tls_verify=True,
                    locality="remote",
                    max_tokens=1024,
                    temperature=0.2,
                    capabilities=(
                        "text_generation",
                        "reasoning",
                        "code",
                        "planning",
                    ),
                ),
                ProviderConfig(
                    provider_id="ollama_local",
                    kind="ollama",
                    enabled=True,
                    endpoint="http://127.0.0.1:11435",
                    model="",
                    timeout_seconds=120.0,
                    connect_timeout_seconds=5.0,
                    max_retries=0,
                    stream=False,
                    api_key_env=None,
                    api_key_required=False,
                    tls_verify=False,
                    locality="local",
                    max_tokens=1024,
                    temperature=0.2,
                    capabilities=(
                        "text_generation",
                        "local_inference",
                    ),
                ),
            ],
            security=SecurityConfig(workspace_root=workspace_root),
        )
        config.validate()
        return config

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        workspace_root: Optional[str] = None,
    ) -> "Phase9Config":
        if not isinstance(payload, Mapping):
            raise ConfigurationError("Configuration root must be a mapping.")

        unknown = set(payload) - {"version", "profile_name", *SECTION_NAMES}
        if unknown:
            raise ConfigurationError(
                "Unknown configuration keys: " + ", ".join(sorted(unknown))
            )

        config = cls()
        config.version = int(payload.get("version", 1))
        config.profile_name = str(payload.get("profile_name", "staging"))

        providers_payload = payload.get("providers", [])
        if not isinstance(providers_payload, list):
            raise ConfigurationError("providers must be a list.")
        config.providers = [
            _build(ProviderConfig, item, "providers")
            for item in providers_payload
        ]

        for section, target in (
            ("resources", ResourceConfig),
            ("security", SecurityConfig),
            ("planning", PlanningConfig),
            ("collaboration", CollaborationConfig),
        ):
            raw = payload.get(section, {})
            if not isinstance(raw, Mapping):
                raise ConfigurationError(f"{section} must be a mapping.")
            setattr(config, section, _build(target, raw, section))

        if workspace_root is not None:
            config.security.workspace_root = workspace_root

        config.validate()
        return config

    @classmethod
    def from_toml(cls, path: str | Path,
                  workspace_root: Optional[str] = None) -> "Phase9Config":
        file_path = Path(path)
        if not file_path.is_file():
            raise ConfigurationError(f"Configuration file not found: {path}")
        try:
            with file_path.open("rb") as handle:
                payload = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigurationError(f"Invalid TOML: {exc}") from exc
        return cls.from_dict(payload, workspace_root=workspace_root)

    @classmethod
    def from_json(cls, path: str | Path,
                  workspace_root: Optional[str] = None) -> "Phase9Config":
        file_path = Path(path)
        if not file_path.is_file():
            raise ConfigurationError(f"Configuration file not found: {path}")
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Invalid JSON: {exc}") from exc
        return cls.from_dict(payload, workspace_root=workspace_root)

    # -- environment overrides --------------------------------------------- #
    def apply_environment(self, environ: Optional[Mapping[str, str]] = None
                          ) -> "Phase9Config":
        """Apply ``PHASE9_*`` overrides. Returns self for chaining."""
        env = environ if environ is not None else os.environ

        def _bool(name: str) -> Optional[bool]:
            raw = env.get(name)
            if raw is None:
                return None
            lowered = raw.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
            raise ConfigurationError(f"{name} must be a boolean-like value.")

        def _int(name: str) -> Optional[int]:
            raw = env.get(name)
            if raw is None:
                return None
            try:
                return int(raw)
            except ValueError as exc:
                raise ConfigurationError(f"{name} must be an integer.") from exc

        def _float(name: str) -> Optional[float]:
            raw = env.get(name)
            if raw is None:
                return None
            try:
                return float(raw)
            except ValueError as exc:
                raise ConfigurationError(f"{name} must be a number.") from exc

        root = env.get("PHASE9_WORKSPACE_ROOT")
        if root:
            self.security.workspace_root = root

        for name, attribute, caster in (
            ("PHASE9_MAX_CONCURRENT_AGENTS", "resources",
             "max_concurrent_agents"),
            ("PHASE9_MAX_CONCURRENT_MODEL_CALLS", "resources",
             "max_concurrent_model_calls"),
            ("PHASE9_MAX_SUBPROCESSES", "resources",
             "max_concurrent_subprocesses"),
        ):
            value = _int(name)
            if value is not None:
                setattr(getattr(self, attribute), caster, value)

        value = _float("PHASE9_MAX_CPU_LOAD_PERCENT")
        if value is not None:
            self.resources.max_cpu_load_percent = value

        value = _float("PHASE9_MIN_FREE_RAM_GB")
        if value is not None:
            self.resources.min_free_ram_gb = value

        for name, attribute in (
            ("PHASE9_BACKUPS_ENABLED", "backups_enabled"),
            ("PHASE9_AUDIT_ENABLED", "audit_enabled"),
            ("PHASE9_FAIL_CLOSED", "fail_closed"),
        ):
            value_bool = _bool(name)
            if value_bool is not None:
                setattr(self.security, attribute, value_bool)

        value = _bool("PHASE9_DEEPSEEK_ENABLED")
        if value is not None:
            deepseek = self.provider("deepseek")
            if deepseek is not None:
                deepseek.enabled = value

        endpoint = env.get("PHASE9_DEEPSEEK_ENDPOINT")
        if endpoint:
            deepseek = self.provider("deepseek")
            if deepseek is not None:
                deepseek.endpoint = endpoint

        model = env.get("PHASE9_DEEPSEEK_MODEL")
        if model:
            deepseek = self.provider("deepseek")
            if deepseek is not None:
                deepseek.model = model

        env_name = env.get("PHASE9_DEEPSEEK_API_KEY_ENV")
        if env_name:
            deepseek = self.provider("deepseek")
            if deepseek is not None:
                deepseek.api_key_env = env_name

        value = _int("PHASE9_PLAN_MAX_STEPS")
        if value is not None:
            self.planning.max_steps = value

        value = _int("PHASE9_REPLAN_LIMIT")
        if value is not None:
            self.planning.replan_limit = value

        value = _int("PHASE9_MAX_DELEGATION_DEPTH")
        if value is not None:
            self.collaboration.max_delegation_depth = value

        self.validate()
        return self

    # -- validation -------------------------------------------------------- #
    def validate(self) -> None:
        if self.version != 1:
            raise ConfigurationError(
                f"Unsupported configuration version: {self.version}"
            )
        seen: set[str] = set()
        for provider in self.providers:
            if not isinstance(provider, ProviderConfig):
                raise ConfigurationError("providers entries must be ProviderConfig.")
            provider.validate()
            if provider.provider_id in seen:
                raise ConfigurationError(
                    f"Duplicate provider id: {provider.provider_id}"
                )
            seen.add(provider.provider_id)

        for section in (
            self.resources,
            self.security,
            self.planning,
            self.collaboration,
        ):
            if not hasattr(section, "validate"):
                raise ConfigurationError("Invalid configuration section.")
            section.validate()

    # -- accessors --------------------------------------------------------- #
    def provider(self, provider_id: str) -> Optional[ProviderConfig]:
        for provider in self.providers:
            if provider.provider_id == provider_id:
                return provider
        return None

    def enabled_providers(self) -> list[ProviderConfig]:
        return [provider for provider in self.providers if provider.enabled]

    def deepseek_enabled(self) -> bool:
        deepseek = self.provider("deepseek")
        return bool(deepseek and deepseek.enabled)

    def redacted_snapshot(self) -> dict[str, Any]:
        """Serialisable snapshot that cannot contain a credential value."""
        return {
            "version": self.version,
            "profile_name": self.profile_name,
            "providers": [p.public_dict() for p in self.providers],
            "resources": asdict(self.resources),
            "security": asdict(self.security),
            "planning": asdict(self.planning),
            "collaboration": asdict(self.collaboration),
        }

    def to_toml(self) -> str:
        """Render a template TOML document that contains no secret values."""
        lines: list[str] = [
            "# Phase 9 staging configuration",
            "# Credentials are NEVER stored in this file.",
            "# Set the environment variable named by api_key_env instead.",
            'profile_name = "%s"' % self.profile_name,
            "",
        ]
        for provider in self.providers:
            lines.append("[[providers]]")
            for key, value in _toml_items(asdict(provider)):
                lines.append(f"{key} = {value}")
            lines.append("")
        for section in (
            ("resources", asdict(self.resources)),
            ("security", asdict(self.security)),
            ("planning", asdict(self.planning)),
            ("collaboration", asdict(self.collaboration)),
        ):
            lines.append(f"[{section[0]}]")
            for key, value in _toml_items(section[1]):
                lines.append(f"{key} = {value}")
            lines.append("")
        return "\n".join(lines)


def _build(target: type, payload: Mapping[str, Any], section: str):
    if not isinstance(payload, Mapping):
        raise ConfigurationError(f"{section} entries must be mappings.")
    allowed = {item.name for item in fields(target)}
    unknown = set(payload) - allowed
    if unknown:
        raise ConfigurationError(
            "Unknown %s keys: %s" % (section, ", ".join(sorted(unknown)))
        )
    data: dict[str, Any] = {}
    for key, value in payload.items():
        data[key] = _coerce(value)
    if target is ProviderConfig and "capabilities" in data:
        data["capabilities"] = tuple(data["capabilities"])
    try:
        return target(**data)
    except TypeError as exc:
        raise ConfigurationError(f"Invalid {section} entry: {exc}") from exc


def _coerce(value: Any) -> Any:
    if isinstance(value, list):
        return [_coerce(item) for item in value]
    return value


def _toml_items(mapping: Mapping[str, Any]):
    for key, value in mapping.items():
        if isinstance(value, (list, tuple)):
            rendered = "[" + ", ".join(
                f'"{item}"' if isinstance(item, str) else str(item).lower()
                for item in value
            ) + "]"
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif value is None:
            continue
        elif isinstance(value, str):
            rendered = '"%s"' % value.replace('"', '\\"')
        else:
            rendered = str(value)
        yield key, rendered


__all__ = [
    "ProviderConfig",
    "ResourceConfig",
    "SecurityConfig",
    "PlanningConfig",
    "CollaborationConfig",
    "Phase9Config",
]
