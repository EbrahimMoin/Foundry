"""StackPack registry and Wave 0 app skeleton implementations."""

from foundry.stack_packs.fastapi_react_postgres import FastAPIReactPostgresPack
from foundry.stack_packs.protocol import StackPack


def get_stack_pack(name: str) -> StackPack:
    packs: dict[str, StackPack] = {
        FastAPIReactPostgresPack.name: FastAPIReactPostgresPack(),
    }
    if name not in packs:
        raise ValueError(f"Unknown stack pack: {name!r}")
    return packs[name]


__all__ = [
    "FastAPIReactPostgresPack",
    "StackPack",
    "get_stack_pack",
]
