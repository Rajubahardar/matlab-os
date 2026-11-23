"""MATLAB Expert Agent Core"""
import logging
import os
import signal
import subprocess
import time
from pathlib import Path

from .skills import register_skills
from ..integration.mcp_client import MCPClient
from matlab_os.memory.database import DatabaseService

logger = logging.getLogger(__name__)


class MATLABAgent:
    """Main MATLAB Expert Agent class."""

    def __init__(self, db_path: str = "data/matlab_agent.db"):
        self.name = "MATLAB Expert Agent"
        self.version = "0.2.0"
        self.skills = []
        self.db_path = db_path
        self.db = None
        self.mcp_client = None
        self.initialized = False
        self._matlab_process = None
        self._matlab_pid = None
        self._owned_by_app = False

    def initialize(self):
        """Initialize the agent and all capabilities."""
        logger.info("Initializing MATLAB Expert Agent...")
        register_skills(self)
        self._init_memory()
        self.initialized = True
        logger.info("MATLAB Expert Agent initialized successfully.")

    def _init_memory(self):
        """Open the single shared SQLite service (schema lives in DatabaseService)."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = DatabaseService(db_path=self.db_path)

    def register_skill(self, skill_name: str):
        """Register a skill with the agent."""
        if skill_name not in self.skills:
            self.skills.append(skill_name)
            logger.info(f"Skill registered: {skill_name}")

    def get_skills(self):
        """Get list of registered skills."""
        return self.skills.copy()

    def _get_matlab_client(self):
        """Return the shared MCP client, reconnecting when disconnected."""

        from ..integration.mcp_client import MCPClient

        if self.mcp_client is not None:
            try:
                if self.mcp_client.is_connected():
                    return self.mcp_client
            except Exception:
                pass

            try:
                self.mcp_client.disconnect()
            except Exception:
                pass
            self.mcp_client = None

        client = MCPClient()
        try:
            client.connect()
            if not client.is_connected():
                raise ConnectionError("MATLAB MCP connection was not established")
            self.mcp_client = client
            logger.info("Persistent MATLAB MCP session established.")
            return client
        except Exception:
            try:
                client.disconnect()
            except Exception:
                pass
            raise

    def build_simulink_model(self, spec):
        """Build a Simulink model from an engineering specification."""

        if not self.initialized:
            raise RuntimeError("Initialize MATLABAgent first.")

        from .simulink_builder import SimulinkModelBuilder
        return SimulinkModelBuilder.execute(self, spec)

    def execute_skill(self, skill_name: str, *args, **kwargs):
        """Route requests through the registered skill handlers."""

        if not self.initialized:
            raise RuntimeError("Initialize MATLABAgent first")

        if skill_name not in self.skills:
            raise ValueError(f"Skill not registered: {skill_name}")

        if (
            skill_name == "Simulink Model Development"
            and kwargs.get("operation") == "build_from_spec"
        ):
            kwargs.pop("operation")
            spec = kwargs.pop("spec", None)
            if spec is None or kwargs:
                raise ValueError("Provide only spec={...} for build_from_spec.")
            return self.build_simulink_model(spec)

        from .skill_handlers import execute_skill_handler

        code = kwargs.pop("code", None)
        if code is None and args:
            code = args[0]

        return execute_skill_handler(
            self, skill_name, code=code, **kwargs
        )

    def execute_matlab_code_interactive(
        self,
        code: str,
        *,
        reason: str = "Interactive MATLAB execution request",
        input_fn=None,
        output_fn=None,
    ):
        """Approve, consume and execute MATLAB code through the persistent MCP client."""

        if not self.initialized or self.db is None:
            raise RuntimeError("Initialize MATLABAgent before execution")

        if not isinstance(code, str) or not code.strip():
            raise ValueError("MATLAB code must be a non-empty string")

        display = print if output_fn is None else output_fn

        if input_fn is not None:
            prompt_input = input_fn
        else:
            def prompt_input(prompt):
                try:
                    with open("CONIN$", "r", encoding="utf-8",
                              errors="replace") as console:
                        display(prompt)
                        return console.readline().strip()
                except OSError:
                    return input(prompt)

        approval_id = self.db.store_approval(
            operation="MATLAB code execution",
            payload=code,
            requested_by="matlab-agent",
            reason=reason,
        )

        display("\n========== MATLAB EXECUTION APPROVAL ==========")
        display(f"Approval ID: {approval_id}")
        display(f"Reason: {reason}")
        display("-----------------------------------------------")
        display(code)
        display("-----------------------------------------------")

        answer = prompt_input("Approve this MATLAB execution? [y/N]: ")
        approved = str(answer).strip().lower() in ("y", "yes")

        self.db.approve_operation(
            approval_id=approval_id,
            approved=approved,
            decided_by="interactive-user",
            reason="Approved interactively" if approved else "Rejected interactively",
        )

        if not approved:
            return {
                "error": True,
                "status": "rejected",
                "approval_id": approval_id,
            }

        # Connect before consuming approval. Connection failures do not consume it.
        client = self._get_matlab_client()

        if not self.db.use_approval(approval_id, code):
            raise PermissionError(
                "Approval invalid, expired, already used, or code has changed."
            )

        try:
            result = client.execute_matlab_code(
                code,
                timeout=90,
                approved=True,
            )
        except Exception:
            # The request may have reached MATLAB even if its response was lost.
            # Never replay it automatically.
            try:
                client.disconnect()
            except Exception:
                pass
            self.mcp_client = None
            raise

        result["approval_id"] = approval_id
        result["status"] = (
            "executed" if not result.get("error") else "execution_error"
        )
        return result

    def close(self):
        """Gracefully close the persistent MCP session and database.

        Only closes sessions owned by the app. Never closes a user's existing
        MATLAB session. Uses a timeout on shutdown to avoid hanging.
        """

        # Only release sessions owned by this app; never close a user session
        if self._owned_by_app:
            client = self.mcp_client
            self.mcp_client = None

            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    logger.exception("MATLAB MCP shutdown encountered an error.")

        if self.db is not None:
            self.db.close()
            self.db = None

        self._matlab_process = None
        self._matlab_pid = None
        self._owned_by_app = False
        self.initialized = False
        logger.info("MATLAB Agent closed.")
