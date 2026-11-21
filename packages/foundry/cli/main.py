"""Foundry CLI entrypoint (`foundry init`, `up`, `down`, `status`, `models`)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from foundry.config.loader import (
    CONFIG_DIR_DEFAULT,
    ConfigError,
    default_foundry_config,
    default_models_config,
    default_project_config,
    load_foundry_config,
    load_models_config,
    save_foundry_config,
    save_models_config,
    save_project_config,
)
from foundry.config.schemas import ModelsConfig
from foundry.gateway.cassette import CassetteStore, MissPolicy
from foundry.gateway.protocol import GatewayRequest, GatewayResponse
from foundry.gateway.router import ModelGateway
from foundry.security.vault import CredentialVault, VaultError

app = typer.Typer(
    name="foundry",
    help="Foundry — local-first, contract-first multi-agent software engineering environment.",
    no_args_is_help=True,
)
models_app = typer.Typer(help="Inspect, configure, and test first-party SDK model providers.")
app.add_typer(models_app, name="models")

console = Console()


def _compose_file(infra_dir: Path | None = None) -> Path:
    base = infra_dir or (Path.cwd() / "infra")
    return base / "docker-compose.yml"


def _load_or_default_models(config_dir: Path) -> ModelsConfig:
    models_path = config_dir / "models.yaml"
    if models_path.exists():
        return load_models_config(models_path)
    return default_models_config()


def _run_compose(args: list[str], compose_path: Path) -> subprocess.CompletedProcess[str]:
    if not compose_path.exists():
        console.print(f"[red]Compose file not found:[/red] {compose_path}")
        raise typer.Exit(code=1)
    docker_bin = shutil.which("docker")
    if docker_bin is None:
        console.print(
            "[red]Docker binary not found on PATH.[/red] "
            "Install Docker Engine inside WSL2 or run in offline mode."
        )
        raise typer.Exit(code=1)
    cmd = [docker_bin, "compose", "-f", str(compose_path), *args]
    return subprocess.run(cmd, check=False, text=True, capture_output=True)  # noqa: S603


@app.command()
def init(
    config_dir: Annotated[
        Path,
        typer.Option(help="Directory for foundry.yaml and models.yaml"),
    ] = CONFIG_DIR_DEFAULT,
    project_dir: Annotated[
        Path | None,
        typer.Option(help="Project root for .foundry/project.yaml (defaults to cwd)"),
    ] = None,
    name: Annotated[str, typer.Option(help="Project name")] = "app",
    force: Annotated[bool, typer.Option(help="Overwrite existing config files")] = False,
) -> None:
    """Initialize global and project configuration files."""
    target_project = project_dir or Path.cwd()
    foundry_path = config_dir / "foundry.yaml"
    models_path = config_dir / "models.yaml"
    project_path = target_project / ".foundry" / "project.yaml"

    if force or not foundry_path.exists():
        save_foundry_config(default_foundry_config(), foundry_path)
        console.print(f"[green]Wrote[/green] {foundry_path}")
    else:
        console.print(f"[yellow]Exists[/yellow] {foundry_path}")

    if force or not models_path.exists():
        save_models_config(default_models_config(), models_path)
        console.print(f"[green]Wrote[/green] {models_path}")
    else:
        console.print(f"[yellow]Exists[/yellow] {models_path}")

    if force or not project_path.exists():
        save_project_config(default_project_config(name=name), project_path)
        console.print(f"[green]Wrote[/green] {project_path}")
    else:
        console.print(f"[yellow]Exists[/yellow] {project_path}")


@app.command()
def up(
    infra_dir: Annotated[Path | None, typer.Option(help="Path to infra directory")] = None,
) -> None:
    """Start local Postgres + egress-proxy infrastructure."""
    result = _run_compose(["up", "-d"], _compose_file(infra_dir))
    if result.returncode != 0:
        console.print(f"[red]docker compose up failed:[/red]\n{result.stderr}")
        raise typer.Exit(code=result.returncode)
    console.print("[green]Foundry infrastructure is running (Postgres + egress-proxy).[/green]")


@app.command()
def down(
    infra_dir: Annotated[Path | None, typer.Option(help="Path to infra directory")] = None,
) -> None:
    """Stop local Foundry infrastructure containers."""
    result = _run_compose(["down"], _compose_file(infra_dir))
    if result.returncode != 0:
        console.print(f"[red]docker compose down failed:[/red]\n{result.stderr}")
        raise typer.Exit(code=result.returncode)
    console.print("[yellow]Foundry infrastructure stopped.[/yellow]")


@app.command()
def status(
    config_dir: Annotated[Path, typer.Option()] = CONFIG_DIR_DEFAULT,
    infra_dir: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Show configuration, vault backend, and local infrastructure status."""
    global_cfg = (
        load_foundry_config(config_dir / "foundry.yaml")
        if (config_dir / "foundry.yaml").exists()
        else default_foundry_config()
    )
    vault = CredentialVault(
        vault_path=Path(global_cfg.secrets.vault_path),
        backend_order=global_cfg.secrets.backend_order,
    )
    console.print(f"Vault backend: [cyan]{vault.active_backend}[/cyan]")
    console.print(
        f"Runtime slots: [cyan]{global_cfg.runtime.max_heavy_exec_slots}[/cyan] heavy exec, "
        f"[cyan]{global_cfg.runtime.max_concurrent_tasks}[/cyan] max concurrent tasks"
    )
    docker_bin = shutil.which("docker")
    compose_path = _compose_file(infra_dir)
    if docker_bin and compose_path.exists():
        res = subprocess.run(  # noqa: S603
            [docker_bin, "compose", "-f", str(compose_path), "ps"],
            check=False,
            text=True,
            capture_output=True,
        )
        console.print(res.stdout or res.stderr)
    else:
        console.print("[yellow]Docker not active; run inside WSL2 with Docker Engine.[/yellow]")


@models_app.command("list")
def models_list(
    config_dir: Annotated[Path, typer.Option()] = CONFIG_DIR_DEFAULT,
) -> None:
    """List first-party SDK providers and role assignments."""
    cfg = _load_or_default_models(config_dir)

    prov_table = Table(title="Configured First-Party SDK Providers")
    prov_table.add_column("Provider ID", style="cyan")
    prov_table.add_column("Transport")
    prov_table.add_column("Billing")
    prov_table.add_column("Data Tier")
    prov_table.add_column("Base URL")
    for pid, pcfg in cfg.providers.items():
        prov_table.add_row(
            pid,
            str(pcfg.transport),
            str(pcfg.billing_mode),
            str(pcfg.data_tier),
            pcfg.base_url or "(official SDK default)",
        )
    console.print(prov_table)

    role_table = Table(title="Role Assignments")
    role_table.add_column("Role", style="green")
    role_table.add_column("Profile")
    role_table.add_column("Provider / Model")
    for role, profile_name in cfg.assignments.roles.items():
        prof = cfg.profiles[profile_name]
        role_table.add_row(role, profile_name, f"{prof.provider} / {prof.model}")
    console.print(role_table)


@models_app.command("assign")
def models_assign(
    role: Annotated[str, typer.Argument(help="Role name (e.g. implementer, test_writer)")],
    profile: Annotated[str, typer.Argument(help="Profile name (e.g. @coder, @verifier)")],
    config_dir: Annotated[Path, typer.Option()] = CONFIG_DIR_DEFAULT,
) -> None:
    """Assign a model profile to an agent role in models.yaml."""
    models_path = config_dir / "models.yaml"
    cfg = _load_or_default_models(config_dir)
    if profile not in cfg.profiles:
        console.print(f"[red]Unknown profile:[/red] {profile}")
        raise typer.Exit(code=1)
    cfg.assignments.roles[role] = profile
    save_models_config(cfg, models_path)
    console.print(f"[green]Assigned[/green] {role} -> {profile} ({models_path})")


@models_app.command("add-key")
def models_add_key(
    provider: Annotated[str, typer.Argument(help="Provider key name (e.g. gemini, openai, xai)")],
    api_key: Annotated[
        str | None,
        typer.Option("--key", help="API key value (prompted securely if omitted)"),
    ] = None,
    passphrase: Annotated[
        str | None,
        typer.Option(help="Vault passphrase if using encrypted_vault"),
    ] = None,
    config_dir: Annotated[Path, typer.Option()] = CONFIG_DIR_DEFAULT,
) -> None:
    """Store an official API key in the OS keyring or encrypted local vault."""
    global_cfg = (
        load_foundry_config(config_dir / "foundry.yaml")
        if (config_dir / "foundry.yaml").exists()
        else default_foundry_config()
    )
    secret_val = api_key or typer.prompt(f"API key for {provider}", hide_input=True)
    vault = CredentialVault(
        vault_path=Path(global_cfg.secrets.vault_path),
        passphrase=passphrase,
        backend_order=global_cfg.secrets.backend_order,
    )
    try:
        backend = vault.set_secret(provider, secret_val)
    except VaultError as exc:
        console.print(f"[red]Vault error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Stored key[/green] '{provider}' using backend '{backend}'.")


@models_app.command("test")
def models_test(
    role: Annotated[str, typer.Option(help="Role to test")] = "implementer",
    replay: Annotated[
        bool,
        typer.Option("--replay", help="Replay from normalized $0 cassette fixture"),
    ] = False,
    cassette_dir: Annotated[
        Path,
        typer.Option(help="Directory containing normalized cassettes"),
    ] = Path("tests/cassettes"),
    passphrase: Annotated[
        str | None,
        typer.Option(help="Vault passphrase if using encrypted_vault"),
    ] = None,
    config_dir: Annotated[Path, typer.Option()] = CONFIG_DIR_DEFAULT,
) -> None:
    """Run a smoke completion through ModelGateway (supports $0 --replay mode)."""
    try:
        cfg = _load_or_default_models(config_dir)
        global_cfg = (
            load_foundry_config(config_dir / "foundry.yaml")
            if (config_dir / "foundry.yaml").exists()
            else default_foundry_config()
        )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    vault = CredentialVault(
        vault_path=Path(global_cfg.secrets.vault_path),
        passphrase=passphrase,
        backend_order=global_cfg.secrets.backend_order,
    )
    cassettes = CassetteStore(cassette_dir, miss_policy=MissPolicy.STRICT) if replay else None
    gateway = ModelGateway(cfg, vault=vault, cassettes=cassettes)
    req = GatewayRequest(
        role=role,
        task_slug="cli_smoke",
        node="cli_smoke",
        step_index=0,
        messages=[
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user", "content": "Reply with OK."},
        ],
    )

    async def _run() -> GatewayResponse:
        try:
            return await gateway.complete(req)
        finally:
            await gateway.aclose()

    try:
        resp = asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]ModelGateway call failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]Success[/green] provider={resp.provider_id} model={resp.model} "
        f"replayed={resp.replayed} cost_usd=${resp.cost_usd:.6f} content={resp.content!r}"
    )
