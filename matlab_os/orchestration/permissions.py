"""Thread-safe exact-match capability permission manager."""


import threading


class PermissionManager:

    def __init__(self, default_allow=False):
        self.default_allow = bool(default_allow)
        self._grants = set()
        self._lock = threading.RLock()

    @staticmethod
    def _key(agent_id, capability, skill, operation):
        values = (agent_id, capability, skill, operation)
        if not all(isinstance(x, str) and x.strip() for x in values):
            raise ValueError("All permission fields must be non-empty strings.")
        return tuple(x.strip() for x in values)

    def grant(self, agent_id, capability, skill, operation):
        with self._lock:
            self._grants.add(
                self._key(agent_id, capability, skill, operation)
            )

    def revoke(self, agent_id, capability, skill, operation):
        with self._lock:
            key = self._key(agent_id, capability, skill, operation)
            existed = key in self._grants
            self._grants.discard(key)
            return existed

    def is_allowed(self, agent_id, capability, skill, operation):
        key = self._key(agent_id, capability, skill, operation)
        with self._lock:
            return self.default_allow or key in self._grants

    def require(self, agent_id, capability, skill, operation):
        if not self.is_allowed(agent_id, capability, skill, operation):
            raise PermissionError(
                "Permission denied: "
                f"{agent_id}/{capability}/{skill}/{operation}"
            )

    def inventory(self):
        with self._lock:
            return sorted(self._grants)

    def count(self):
        with self._lock:
            return len(self._grants)

    def clear(self):
        with self._lock:
            self._grants.clear()
