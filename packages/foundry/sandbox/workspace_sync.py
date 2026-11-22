"""Bi-directional .git-free workspace sync between daemon worktrees and container dirs."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from foundry.security.workspace_guard import (
    ForbiddenGitMetaError,
    WriteSetViolationError,
    is_forbidden_git_meta,
    normalize_repo_path,
    path_in_write_set,
    validate_workspace_tree,
)

_IGNORED_CACHE_DIRS = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".hypothesis",
        ".foundry_tmp",
        "node_modules",
    }
)


def _file_hashes(root: Path, *, ignore_git: bool) -> dict[str, str]:
    """Return mapping of normalized relative file path -> SHA-256 hex digest."""
    hashes: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _IGNORED_CACHE_DIRS and not (ignore_git and current == root and d == ".git")
        ]
        if ignore_git and current == root and ".git" in filenames:
            filenames.remove(".git")
        for fname in filenames:
            fpath = current / fname
            rel = normalize_repo_path(fpath.relative_to(root).as_posix())
            hashes[rel] = hashlib.sha256(fpath.read_bytes()).hexdigest()
    return hashes


def sync_worktree_to_workspace(worktree_dir: Path, workspace_dir: Path) -> None:
    """Mirror daemon worktree into a .git-free workspace dir for container mounting."""
    validate_workspace_tree(worktree_dir, ignore_git_dir=True)
    if workspace_dir.exists():
        for child in workspace_dir.iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
    else:
        workspace_dir.mkdir(parents=True, exist_ok=True)

    for dirpath, dirnames, filenames in os.walk(worktree_dir, topdown=True, followlinks=False):
        current = Path(dirpath)
        if current == worktree_dir and ".git" in dirnames:
            dirnames.remove(".git")
        if current == worktree_dir and ".git" in filenames:
            filenames.remove(".git")

        rel_dir = current.relative_to(worktree_dir)
        target_dir = workspace_dir / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        for fname in filenames:
            shutil.copy2(current / fname, target_dir / fname)


def sync_workspace_to_worktree(
    workspace_dir: Path,
    worktree_dir: Path,
    write_set: Sequence[str],
    *,
    allow_infra: bool = False,
) -> list[str]:
    """Validate symlinks/FIFOs/YAML bombs & write sets before copying container edits back."""
    validate_workspace_tree(workspace_dir, ignore_git_dir=False)

    src_hashes = _file_hashes(workspace_dir, ignore_git=False)
    dst_hashes = _file_hashes(worktree_dir, ignore_git=True)

    changed: list[str] = []
    for rel, src_digest in src_hashes.items():
        if dst_hashes.get(rel) != src_digest:
            changed.append(rel)
    for rel in dst_hashes:
        if rel not in src_hashes:
            changed.append(rel)

    changed.sort()
    for rel in changed:
        if is_forbidden_git_meta(rel, allow_infra=allow_infra):
            raise ForbiddenGitMetaError(f"Forbidden git metadata edit inside sandbox: {rel}")
    offending = [rel for rel in changed if not path_in_write_set(rel, write_set)]
    if offending:
        raise WriteSetViolationError(offending, write_set)

    # Atomic pre-validation passed; apply file additions, updates, and deletions
    for rel in changed:
        src = workspace_dir / rel
        dst = worktree_dir / rel
        if rel in src_hashes:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif dst.exists():
            dst.unlink()

    return changed
