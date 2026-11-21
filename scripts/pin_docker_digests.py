"""Resolve and print immutable sha256 manifest digests for Foundry infrastructure images."""

from __future__ import annotations

import json
import sys
import urllib.request

IMAGES: list[tuple[str, str]] = [
    ("pgvector/pgvector", "pg16"),
    ("monokal/tinyproxy", "latest"),
]

ACCEPT_HEADER = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


def resolve_digest(repo: str, tag: str) -> str:
    """Query Docker Hub Registry v2 API for the immutable manifest digest."""
    token_url = (
        f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
    )
    with urllib.request.urlopen(token_url, timeout=15) as resp:  # noqa: S310
        token = str(json.loads(resp.read().decode("utf-8"))["token"])

    manifest_url = f"https://registry-1.docker.io/v2/{repo}/manifests/{tag}"
    req = urllib.request.Request(
        manifest_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": ACCEPT_HEADER,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        digest = resp.headers.get("Docker-Content-Digest")
    if not digest:
        raise RuntimeError(f"Missing Docker-Content-Digest header for {repo}:{tag}")
    return str(digest)


def main() -> int:
    for repo, tag in IMAGES:
        digest = resolve_digest(repo, tag)
        print(f"{repo}:{tag}@{digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
