"""Workspace path validation and safe file primitives.

This module is the security boundary for every Phase 9 filesystem operation.

Threat model addressed
----------------------
* ``..`` traversal and encoded traversal attempts.
* Absolute paths, UNC paths, ``\\\\?\\`` device paths and NTFS alternate data
  streams escaping the workspace.
* Symbolic links and Windows reparse points (junctions, mount points) that
  redirect a nominally in-workspace path outside the boundary.
* Windows reserved device names (``CON``, ``NUL``, ``COM1`` ...) and names with
  trailing dots/spaces that the Win32 layer silently normalises.
* Path-length and component-count abuse.
* Read/write through a path that is swapped between validation and use
  (best-effort TOCTOU mitigation, see the documented limitation in
  ``docs/SECURITY_PHASE9.md``).

Unsupported by design
---------------------
Full handle-relative native filesystem access is **not** implemented in this
staging phase.  ``os.open`` with ``O_NOFOLLOW`` is used where the platform
supports it, the opened handle identity is re-verified against the validated
path, and the residual TOCTOU window is recorded as a known limitation rather
than being claimed closed.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..errors import PathSecurityError, WorkspaceBoundaryError

#: Win32 reparse point attribute (mirrors FILE_ATTRIBUTE_REPARSE_POINT).
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400

RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CLOCK$"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)

MAX_PATH_LENGTH = 4096
MAX_COMPONENT_LENGTH = 255

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)


@dataclass(frozen=True)
class ResolvedPath:
    """A workspace path that has passed every validation layer."""

    absolute: Path
    relative: str
    exists: bool
    is_file: bool
    is_dir: bool
    size: Optional[int]
    validated_at: float

    def describe(self) -> dict[str, object]:
        return {
            "relative": self.relative,
            "exists": self.exists,
            "is_file": self.is_file,
            "is_dir": self.is_dir,
            "size": self.size,
        }


def sha256_file(path: Path, chunk_bytes: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_bytes), b""):
            digest.update(block)
    return digest.hexdigest()


class PathGuard:
    """Validate and resolve paths strictly inside one workspace root."""

    def __init__(
        self,
        workspace_root: str | os.PathLike[str],
        *,
        allow_symlinks: bool = False,
        allow_reparse_points: bool = False,
        allow_absolute_operation_paths: bool = False,
        max_path_components: int = 32,
        max_file_bytes: int = 2_000_000,
        reserved_prefixes: tuple[str, ...] = (),
    ) -> None:
        if workspace_root is None:
            raise WorkspaceBoundaryError("A workspace root is required.")

        raw_root = Path(os.fspath(workspace_root))
        if not raw_root.is_absolute():
            raise WorkspaceBoundaryError(
                "Workspace root must be an absolute path.",
                root=str(raw_root),
            )
        if not raw_root.exists():
            raise WorkspaceBoundaryError(
                "Workspace root does not exist.", root=str(raw_root)
            )
        if not raw_root.is_dir():
            raise WorkspaceBoundaryError(
                "Workspace root must be a directory.", root=str(raw_root)
            )

        self.root = raw_root.resolve(strict=True)
        self._root_key = self._key(self.root)
        self.allow_symlinks = bool(allow_symlinks)
        self.allow_reparse_points = bool(allow_reparse_points)
        self.allow_absolute_operation_paths = bool(
            allow_absolute_operation_paths
        )
        self.max_path_components = int(max_path_components)
        self.max_file_bytes = int(max_file_bytes)

        self.reserved_prefixes: tuple[str, ...] = tuple(
            str(item).strip().strip("/\\")
            for item in reserved_prefixes
            if str(item).strip()
        )

        if not 1 <= self.max_path_components <= 128:
            raise WorkspaceBoundaryError(
                "max_path_components must be within [1, 128]."
            )
        if self.max_file_bytes < 1:
            raise WorkspaceBoundaryError("max_file_bytes must be positive.")

    # -- helpers ----------------------------------------------------------- #
    @staticmethod
    def _key(path: Path) -> str:
        """Case-normalised absolute key used for containment comparison."""
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    def _contains(self, candidate: Path) -> bool:
        candidate_key = self._key(candidate)
        if candidate_key == self._root_key:
            return True
        root_prefix = self._root_key
        if not root_prefix.endswith(os.sep):
            root_prefix = root_prefix + os.sep
        return candidate_key.startswith(root_prefix)

    def _reject(self, message: str, **context: object) -> None:
        raise PathSecurityError(message, **context)

    # -- lexical validation ------------------------------------------------ #
    def _validate_lexical(self, candidate: str) -> tuple[str, list[str]]:
        if not isinstance(candidate, str):
            self._reject("Path must be a string.", value=type(candidate).__name__)
        if not candidate.strip():
            self._reject("Path must not be empty.")
        if "\x00" in candidate:
            self._reject("Path contains a NUL byte.")
        for char in candidate:
            if ord(char) < 32:
                self._reject(
                    "Path contains a control character.",
                    char_code=ord(char),
                )
        if len(candidate) > MAX_PATH_LENGTH:
            self._reject("Path exceeds the maximum supported length.",
                         length=len(candidate))

        normalised = candidate.replace("\\", "/")

        # Device and UNC prefixes must never be accepted.
        if normalised.startswith("//") or candidate.startswith(("\\\\", "//")):
            self._reject("UNC and device paths are not permitted.")
        if candidate.startswith("\\\\.\\") or candidate.startswith("\\??\\"):
            self._reject("Win32 device namespace paths are not permitted.")

        # ``os.path.isabs`` is drive-aware on Windows and reports False for
        # "/etc/passwd" (a drive-relative rooted path), so a leading separator
        # must be rejected explicitly.  Otherwise a rooted POSIX path would be
        # silently reinterpreted as workspace-relative.
        rooted = candidate[:1] in ("/", "\\")
        drive_qualified = bool(len(candidate) > 1 and candidate[1] == ":")
        is_absolute = os.path.isabs(candidate) or rooted or drive_qualified
        if rooted:
            self._reject(
                "Rooted paths are not permitted; use a path relative to the "
                "workspace root."
            )
        if drive_qualified:
            self._reject("Drive-qualified paths are not permitted.")
        if is_absolute and not self.allow_absolute_operation_paths:
            self._reject(
                "Absolute paths are not permitted; use a path relative to "
                "the workspace root."
            )

        steps = [part for part in normalised.split("/") if part not in ("", ".")]
        if not steps:
            self._reject("Path resolves to the workspace root itself.")
        if len(steps) > self.max_path_components:
            self._reject(
                "Path exceeds the maximum component count.",
                components=len(steps),
            )

        if self.reserved_prefixes and steps[0] in self.reserved_prefixes:
            self._reject(
                "Path targets a reserved internal workspace directory.",
                component=steps[0],
            )

        for part in steps:
            if part == "..":
                self._reject("Parent-directory traversal is not permitted.")
            if len(part) > MAX_COMPONENT_LENGTH:
                self._reject("Path component is too long.",
                             length=len(part))
            if os.name == "nt":
                if ":" in part:
                    self._reject(
                        "Colons are not permitted in path components "
                        "(alternate data streams).",
                    )
                if part != part.rstrip(" ."):
                    self._reject(
                        "Path components must not end with a space or dot.",
                    )
                stem = part.split(".")[0].upper()
                if stem in RESERVED_DEVICE_NAMES:
                    self._reject(
                        "Reserved Windows device name is not permitted.",
                        component=part,
                    )

        relative = "/".join(steps)
        return relative, steps

    # -- reparse / symlink inspection -------------------------------------- #
    def _inspect_existing_components(self, relative_parts: list[str]) -> None:
        current = self.root
        for part in relative_parts:
            current = current / part
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                return
            except OSError as exc:
                self._reject(
                    "Unable to inspect path component.",
                    component=part,
                    error=exc.strerror or "unknown",
                )
                return

            if stat.S_ISLNK(info.st_mode):
                if not self.allow_symlinks:
                    self._reject(
                        "Symbolic links are not permitted inside the "
                        "workspace.",
                        component=part,
                    )

            if os.name == "nt":
                attributes = getattr(info, "st_file_attributes", 0)
                if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                    if not self.allow_reparse_points:
                        self._reject(
                            "Windows reparse points (junctions or mount "
                            "points) are not permitted inside the workspace.",
                            component=part,
                        )

    # -- public resolution ------------------------------------------------- #
    def resolve(
        self,
        candidate: str | os.PathLike[str],
        *,
        must_exist: bool = False,
        want: str = "any",
    ) -> ResolvedPath:
        """Validate *candidate* and return its resolved workspace path.

        ``want`` is one of ``"any"``, ``"file"`` or ``"dir"``.
        """
        if want not in {"any", "file", "dir"}:
            raise ValueError("want must be 'any', 'file' or 'dir'.")

        text = os.fspath(candidate)
        if isinstance(text, bytes):
            self._reject("Path must be text, not bytes.")

        if os.path.isabs(text) and self.allow_absolute_operation_paths:
            # Absolute input is only accepted when it is already inside root;
            # convert to a relative form and continue with the same checks.
            absolute_text = Path(text).resolve(strict=False)
            if not self._contains(absolute_text):
                raise WorkspaceBoundaryError(
                    "Absolute path is outside the workspace.",
                    path=str(text),
                )
            text = os.path.relpath(absolute_text, self.root)

        relative, parts = self._validate_lexical(text)

        lexical = self.root.joinpath(*parts)
        if not self._contains(lexical):
            raise WorkspaceBoundaryError(
                "Lexical path escapes the workspace.", path=relative
            )

        self._inspect_existing_components(parts)

        try:
            resolved = lexical.resolve(strict=False)
        except OSError as exc:  # pragma: no cover - platform specific
            self._reject("Path could not be resolved.",
                         path=relative, error=exc.strerror or "unknown")

        if not self._contains(resolved):
            raise WorkspaceBoundaryError(
                "Resolved path escapes the workspace.", path=relative
            )

        # Re-validate the resolved form: a symlink chain could have introduced
        # a forbidden component even when the lexical form looked clean.
        if self._key(resolved) != self._key(lexical):
            self._inspect_existing_components(
                [part for part in resolved.relative_to(self.root).parts]
            )

        exists = resolved.exists()
        is_file = exists and resolved.is_file()
        is_dir = exists and resolved.is_dir()

        if must_exist and not exists:
            from ..errors import WorkspaceError

            raise WorkspaceError(
                "Path does not exist.", path=relative
            )
        if want == "file":
            if not exists:
                from ..errors import WorkspaceError

                raise WorkspaceError("File does not exist.", path=relative)
            if not is_file:
                from ..errors import WorkspaceError

                raise WorkspaceError("Path is not a regular file.",
                                     path=relative)
        if want == "dir":
            if not exists:
                from ..errors import WorkspaceError

                raise WorkspaceError("Directory does not exist.", path=relative)
            if not is_dir:
                from ..errors import WorkspaceError

                raise WorkspaceError("Path is not a directory.", path=relative)

        size: Optional[int] = None
        if is_file:
            try:
                size = resolved.stat().st_size
            except OSError:  # pragma: no cover - platform specific
                size = None
            if size is not None and size > self.max_file_bytes:
                from ..errors import OperationLimitError

                raise OperationLimitError(
                    "File exceeds the configured size limit.",
                    path=relative,
                    size=size,
                    limit=self.max_file_bytes,
                )

        return ResolvedPath(
            absolute=resolved,
            relative=relative,
            exists=exists,
            is_file=is_file,
            is_dir=is_dir,
            size=size,
            validated_at=time.time(),
        )

    def resolve_relative(self, candidate: str | os.PathLike[str]) -> str:
        """Return only the validated workspace-relative POSIX path string."""
        return self.resolve(candidate).relative

    # -- directory creation ------------------------------------------------ #
    def ensure_directory(self, relative: str) -> ResolvedPath:
        """Create *relative* as a directory (parents included) when safe."""
        resolved = self.resolve(relative, want="any")
        if resolved.exists:
            if not resolved.is_dir:
                from ..errors import WorkspaceError

                raise WorkspaceError(
                    "Path exists and is not a directory.",
                    path=resolved.relative,
                )
            return resolved

        directory = resolved.absolute
        parent = directory.parent
        # Validate every ancestor before creating anything.
        while parent != self.root:
            if not self._contains(parent):
                raise WorkspaceBoundaryError(
                    "Directory creation escaped the workspace.",
                    path=resolved.relative,
                )
            parent = parent.parent

        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            from ..errors import WorkspaceError

            raise WorkspaceError(
                "Directory could not be created.",
                path=resolved.relative,
                error=exc.strerror or "unknown",
            ) from exc

        return self.resolve(relative, must_exist=True, want="dir")

    # -- read / write primitives ------------------------------------------- #
    def read_bytes(self, relative: str) -> tuple[bytes, str]:
        """Read a validated regular file, returning ``(bytes, sha256)``."""
        resolved = self.resolve(relative, want="file")
        data = self._read_verified(resolved)
        return data, hashlib.sha256(data).hexdigest()

    def read_text(self, relative: str) -> tuple[str, str]:
        data, digest = self.read_bytes(relative)
        try:
            return data.decode("utf-8"), digest
        except UnicodeDecodeError as exc:
            from ..errors import WorkspaceError

            raise WorkspaceError(
                "File is not valid UTF-8 text.",
                path=relative,
                offset=exc.start,
            ) from exc

    def _read_verified(self, resolved: ResolvedPath) -> bytes:
        flags = os.O_RDONLY | _O_BINARY | _O_NOFOLLOW
        try:
            descriptor = os.open(os.fspath(resolved.absolute), flags)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                self._reject(
                    "File changed into a symbolic link after validation.",
                    path=resolved.relative,
                )
            from ..errors import WorkspaceError

            raise WorkspaceError(
                "File could not be opened.",
                path=resolved.relative,
                error=exc.strerror or "unknown",
            ) from exc

        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                self._reject(
                    "Opened object is not a regular file.",
                    path=resolved.relative,
                )
            identity_before = self._identity(opened)
            on_disk = os.stat(os.fspath(resolved.absolute))
            if self._identity(on_disk) != identity_before:
                self._reject(
                    "File identity changed between validation and read.",
                    path=resolved.relative,
                )
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                data = handle.read(self.max_file_bytes + 1)
        finally:
            os.close(descriptor)

        if len(data) > self.max_file_bytes:
            from ..errors import OperationLimitError

            raise OperationLimitError(
                "File exceeds the configured size limit.",
                path=resolved.relative,
                limit=self.max_file_bytes,
            )
        return data

    @staticmethod
    def _identity(info: os.stat_result) -> tuple[int, int, int]:
        return (info.st_dev, info.st_ino, info.st_size)

    def atomic_write_bytes(
        self,
        relative: str,
        data: bytes,
        *,
        create_parents: bool = True,
    ) -> tuple[ResolvedPath, str]:
        """Atomically write *data* to a validated workspace path.

        The temporary file is created inside the same validated directory so
        ``os.replace`` stays on one volume.  Returns the resolved path and the
        SHA-256 of the bytes written.
        """
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes.")
        payload = bytes(data)
        if len(payload) > self.max_file_bytes:
            from ..errors import OperationLimitError

            raise OperationLimitError(
                "Payload exceeds the configured size limit.",
                size=len(payload),
                limit=self.max_file_bytes,
            )

        target = self.resolve(relative, want="any")
        parent_relative = os.path.dirname(target.relative) or "."
        if create_parents:
            if parent_relative != ".":
                self.ensure_directory(parent_relative)
        elif not target.absolute.parent.exists():
            from ..errors import WorkspaceError

            raise WorkspaceError(
                "Parent directory does not exist.",
                path=target.relative,
            )

        # Re-resolve after directory creation so the final target is validated.
        target = self.resolve(relative, want="any")
        if target.exists and target.is_dir:
            from ..errors import WorkspaceError

            raise WorkspaceError(
                "Cannot write over a directory.", path=target.relative
            )

        directory = target.absolute.parent
        temporary_name: Optional[str] = None
        try:
            handle, temporary_name = tempfile.mkstemp(
                prefix=".phase9_", suffix=".tmp", dir=str(directory)
            )
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # Validate the temporary file itself before replacing the target.
            temporary_resolved = self.resolve(
                os.path.relpath(temporary_name, self.root), want="file"
            )
            if temporary_resolved.absolute != Path(temporary_name).resolve():
                self._reject("Temporary file identity changed.")

            os.replace(temporary_name, target.absolute)
            temporary_name = None
        except OSError as exc:
            from ..errors import WorkspaceError

            raise WorkspaceError(
                "Atomic write failed.",
                path=target.relative,
                error=exc.strerror or "unknown",
            ) from exc
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except OSError:  # pragma: no cover - best effort cleanup
                    pass

        return self.resolve(relative, must_exist=True, want="file"), hashlib.sha256(
            payload
        ).hexdigest()

    def atomic_write_text(
        self,
        relative: str,
        text: str,
        *,
        create_parents: bool = True,
        newline: Optional[str] = "\n",
    ) -> tuple[ResolvedPath, str]:
        if not isinstance(text, str):
            raise TypeError("text must be a string.")
        if newline:
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            if newline != "\n":
                text = text.replace("\n", newline)
        return self.atomic_write_bytes(
            relative, text.encode("utf-8"), create_parents=create_parents
        )

    # -- deletion ---------------------------------------------------------- #
    def remove_file(self, relative: str) -> str:
        """Remove one validated *regular file*.

        Refuses directories, symbolic links and reparse points.  Deletion is
        only reachable from ``WorkspaceManager.delete`` after the policy and
        approval checks succeed; nothing in Phase 9 calls this implicitly.
        """
        resolved = self.resolve(relative, want="file")
        info = os.lstat(os.fspath(resolved.absolute))
        if stat.S_ISLNK(info.st_mode):
            self._reject(
                "Refusing to delete a symbolic link.", path=resolved.relative
            )
        if os.name == "nt" and (
            getattr(info, "st_file_attributes", 0)
            & FILE_ATTRIBUTE_REPARSE_POINT
        ):
            self._reject(
                "Refusing to delete a reparse point.", path=resolved.relative
            )
        if not stat.S_ISREG(info.st_mode):
            self._reject(
                "Refusing to delete a non-regular file.",
                path=resolved.relative,
            )
        try:
            os.unlink(os.fspath(resolved.absolute))
        except OSError as exc:
            from ..errors import WorkspaceError

            raise WorkspaceError(
                "File could not be removed.",
                path=resolved.relative,
                error=exc.strerror or "unknown",
            ) from exc
        return resolved.relative


__all__ = [
    "FILE_ATTRIBUTE_REPARSE_POINT",
    "RESERVED_DEVICE_NAMES",
    "ResolvedPath",
    "PathGuard",
    "sha256_file",
    "MAX_PATH_LENGTH",
    "MAX_COMPONENT_LENGTH",
]
