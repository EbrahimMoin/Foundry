"""Workspace safety guards: path normalization, write-set checks, symlink/FIFO/YAML-bomb walks."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Sequence
from pathlib import Path

_YAML_ALIAS_RE = re.compile(r"(?:^|[\s,\[\{])\*[A-Za-z0-9_-]+")
_YAML_ANCHOR_RE = re.compile(r"(?:^|[\s,\[\{:])&[A-Za-z0-9_-]+")
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


class WorkspaceViolationError(Exception):
    """Base exception for unsafe workspace files or out-of-scope edits."""


class PathTraversalError(WorkspaceViolationError):
    """Raised on empty, absolute, NUL-byte, or '..' escaping paths."""


class SymlinkViolationError(WorkspaceViolationError):
    """Raised when a symlink or git mode 120000 entry is detected."""


class SpecialFileViolationError(WorkspaceViolationError):
    """Raised when a FIFO, socket, or device node is detected."""


class YamlBombError(WorkspaceViolationError):
    """Raised when an oversized or alias-heavy YAML file is detected."""


class ForbiddenGitMetaError(WorkspaceViolationError):
    """Raised when a forbidden .git* metadata file is touched."""


class WriteSetViolationError(WorkspaceViolationError):
    """Raised when modified paths fall outside the task's reserved write set."""

    def __init__(self, offending_paths: Sequence[str], write_set: Sequence[str]) -> None:
        self.offending_paths = list(offending_paths)
        self.write_set = list(write_set)
        super().__init__(
            f"Paths outside write set {list(write_set)}: {', '.join(self.offending_paths)}"
        )


def normalize_repo_path(path: str, *, allow_directory_slash: bool = False) -> str:
    """Normalize a repository-relative path to POSIX form and block traversal."""
    if not path or "\x00" in path:
        raise PathTraversalError(f"Invalid empty or NUL path: {path!r}")

    posix = path.replace("\\", "/")
    while posix.startswith("./"):
        posix = posix[2:]

    if posix.startswith("/") or _DRIVE_PREFIX_RE.match(posix):
        raise PathTraversalError(f"Absolute paths are forbidden: {path!r}")

    trailing_slash = posix.endswith("/")
    if trailing_slash and not allow_directory_slash:
        raise PathTraversalError(f"File path cannot end with '/': {path!r}")

    raw_parts = [part for part in posix.split("/") if part]
    if not raw_parts:
        raise PathTraversalError(f"Path resolves to repository root: {path!r}")
    for part in raw_parts:
        if part in {".", ".."}:
            raise PathTraversalError(f"Path traversal segment not allowed: {path!r}")

    normalized = "/".join(raw_parts)
    return f"{normalized}/" if trailing_slash else normalized


def is_forbidden_git_meta(path: str, *, allow_infra: bool = False) -> bool:
    """Return True if path touches .git internals or (for non-infra) .git* config files."""
    norm = normalize_repo_path(path, allow_directory_slash=True).rstrip("/")
    parts = norm.split("/")
    for part in parts:
        if part == ".git":
            return True
        if not allow_infra and part != ".github" and part.startswith(".git"):
            return True
    return False


def path_in_write_set(path: str, write_set: Sequence[str]) -> bool:
    """Return True if normalized path matches an exact entry or directory prefix in write_set."""
    norm_path = normalize_repo_path(path, allow_directory_slash=True)
    path_no_slash = norm_path.rstrip("/")
    for raw_entry in write_set:
        entry = normalize_repo_path(raw_entry, allow_directory_slash=True)
        if entry.endswith("/"):
            if norm_path.startswith(entry) or path_no_slash == entry.rstrip("/"):
                return True
        elif path_no_slash == entry:
            return True
    return False


def paths_overlap(paths_a: Sequence[str], paths_b: Sequence[str]) -> list[tuple[str, str]]:
    """Return pairs of paths that conflict under exact match or directory-prefix rules."""
    norm_a = [normalize_repo_path(p, allow_directory_slash=True) for p in paths_a]
    norm_b = [normalize_repo_path(p, allow_directory_slash=True) for p in paths_b]
    conflicts: list[tuple[str, str]] = []
    for a in norm_a:
        for b in norm_b:
            a_dir = a.endswith("/")
            b_dir = b.endswith("/")
            same = a.rstrip("/") == b.rstrip("/") and (a_dir == b_dir or a_dir or b_dir)
            prefix_a = a_dir and b.startswith(a)
            prefix_b = b_dir and a.startswith(b)
            if same or prefix_a or prefix_b:
                conflicts.append((a, b))
    return conflicts


def _check_yaml_safety(file_path: Path, size_bytes: int, max_bytes: int, max_aliases: int) -> None:
    if size_bytes > max_bytes:
        raise YamlBombError(
            f"YAML file '{file_path}' exceeds size limit ({size_bytes} > {max_bytes} bytes)"
        )
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise WorkspaceViolationError(f"Failed to read YAML file '{file_path}': {exc}") from exc

    aliases = _YAML_ALIAS_RE.findall(content)
    if len(aliases) > max_aliases:
        raise YamlBombError(
            f"YAML file '{file_path}' exceeds alias limit ({len(aliases)} > {max_aliases})"
        )
    anchors = _YAML_ANCHOR_RE.findall(content)
    if len(anchors) > 16 and len(aliases) >= 4 * len(anchors):
        raise YamlBombError(f"Potential YAML anchor/alias expansion bomb in '{file_path}'")


def _check_stat(path: Path, st: os.stat_result) -> None:
    if stat.S_ISLNK(st.st_mode):
        raise SymlinkViolationError(f"Symlink detected and rejected: {path}")
    if (
        stat.S_ISFIFO(st.st_mode)
        or stat.S_ISSOCK(st.st_mode)
        or stat.S_ISBLK(st.st_mode)
        or stat.S_ISCHR(st.st_mode)
    ):
        raise SpecialFileViolationError(f"Special device/FIFO/socket rejected: {path}")


def validate_workspace_tree(
    root: Path,
    *,
    max_yaml_bytes: int = 1_048_576,
    max_yaml_aliases: int = 32,
    ignore_git_dir: bool = False,
) -> None:
    """Recursively reject symlinks, FIFOs/devices, and YAML bombs before any host tool reads."""
    if not root.exists() and not root.is_symlink():
        raise WorkspaceViolationError(f"Workspace path does not exist: {root}")
    root_stat = os.lstat(root)
    _check_stat(root, root_stat)

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(dirpath)
        if ignore_git_dir and current == root and ".git" in dirnames:
            dirnames.remove(".git")
        if ignore_git_dir and current == root and ".git" in filenames:
            filenames.remove(".git")

        for dname in list(dirnames):
            dpath = current / dname
            st = os.lstat(dpath)
            _check_stat(dpath, st)

        for fname in filenames:
            fpath = current / fname
            st = os.lstat(fpath)
            _check_stat(fpath, st)
            if fpath.suffix.lower() in {".yaml", ".yml"}:
                _check_yaml_safety(fpath, st.st_size, max_yaml_bytes, max_yaml_aliases)
