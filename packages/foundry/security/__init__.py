"""Security primitives: encrypted API key vault, secret scanner, workspace guard, hardened git."""

from foundry.security.hardened_git import GitError, HardenedGit
from foundry.security.scanner import SecretLeakError, SecretMatch, SecretScanner
from foundry.security.vault import CredentialVault, VaultError
from foundry.security.workspace_guard import (
    ForbiddenGitMetaError,
    PathTraversalError,
    SpecialFileViolationError,
    SymlinkViolationError,
    WorkspaceViolationError,
    WriteSetViolationError,
    YamlBombError,
    is_forbidden_git_meta,
    normalize_repo_path,
    path_in_write_set,
    paths_overlap,
    validate_workspace_tree,
)

__all__ = [
    "CredentialVault",
    "ForbiddenGitMetaError",
    "GitError",
    "HardenedGit",
    "PathTraversalError",
    "SecretLeakError",
    "SecretMatch",
    "SecretScanner",
    "SpecialFileViolationError",
    "SymlinkViolationError",
    "VaultError",
    "WorkspaceViolationError",
    "WriteSetViolationError",
    "YamlBombError",
    "is_forbidden_git_meta",
    "normalize_repo_path",
    "path_in_write_set",
    "paths_overlap",
    "validate_workspace_tree",
]
