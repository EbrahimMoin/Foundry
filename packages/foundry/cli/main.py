"""Foundry CLI entrypoint (`foundry init`, `up`, `down`, `status`, `models`)."""

from __future__ import annotations

import asyncio
import importlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from rich.console import Console
from rich.table import Table

from foundry.agents.single import ReplayMockSandboxProvider, SingleAgentRunner
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
from foundry.config.schemas import BudgetConfig, ModelsConfig
from foundry.gateway.cassette import CassetteStore, MissPolicy
from foundry.gateway.protocol import GatewayRequest, GatewayResponse
from foundry.gateway.router import ModelGateway
from foundry.sandbox import HeavyExecSemaphore, LocalDockerProvider, SandboxProvider
from foundry.security.vault import CredentialVault, VaultError
from foundry.stack_packs import get_stack_pack

app = typer.Typer(
    name="foundry",
    help="Foundry — local-first, contract-first multi-agent software engineering environment.",
    no_args_is_help=True,
)
models_app = typer.Typer(help="Inspect, configure, and test first-party SDK model providers.")
stack_app = typer.Typer(help="Inspect and scaffold stack-pack Wave 0 skeletons.")
sandbox_app = typer.Typer(help="Check sandbox runtime, Docker Engine, and workspace guards.")
bench_app = typer.Typer(help="Run foundry-bench baselines and evaluation suites.")
app.add_typer(models_app, name="models")
app.add_typer(stack_app, name="stack")
app.add_typer(sandbox_app, name="sandbox")
app.add_typer(bench_app, name="bench")

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


@stack_app.command("scaffold")
def stack_scaffold(
    target_dir: Annotated[Path, typer.Argument(help="Destination directory for Wave 0 app")],
    name: Annotated[str, typer.Option(help="Application name")] = "app",
    pack: Annotated[str, typer.Option(help="Stack pack name")] = "fastapi-react-postgres",
) -> None:
    """Materialize a Wave 0 skeleton using a Foundry StackPack."""
    try:
        stack_pack = get_stack_pack(pack)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    written = stack_pack.scaffold(target_dir, app_name=name)
    console.print(
        f"[green]Scaffolded[/green] {len(written)} files for '{name}' "
        f"({stack_pack.name}) at {target_dir}"
    )


@sandbox_app.command("check")
def sandbox_check(
    infra_dir: Annotated[Path | None, typer.Option(help="Path to infra directory")] = None,
) -> None:
    """Verify Docker Engine availability, compose file, and sandbox Dockerfile readiness."""
    infra = infra_dir or (Path.cwd() / "infra")
    compose_ok = (infra / "docker-compose.yml").exists()
    dockerfile_ok = (infra / "sandbox.Dockerfile").exists()
    docker_bin = shutil.which("docker")
    git_bin = shutil.which("git")

    console.print(f"Daemon git binary: [cyan]{git_bin or 'missing'}[/cyan]")
    console.print(f"Docker CLI binary: [cyan]{docker_bin or 'not installed in WSL2'}[/cyan]")
    console.print(
        f"infra/docker-compose.yml: [cyan]{'present' if compose_ok else 'missing'}[/cyan]"
    )
    console.print(
        f"infra/sandbox.Dockerfile: [cyan]{'present' if dockerfile_ok else 'missing'}[/cyan]"
    )

    if docker_bin:
        info_res = subprocess.run(  # noqa: S603
            [docker_bin, "info", "--format", "{{.ServerVersion}}"],
            check=False,
            text=True,
            capture_output=True,
        )
        daemon_state = (
            f"connected (v{info_res.stdout.strip()})"
            if info_res.returncode == 0
            else f"unavailable ({info_res.stderr.strip() or 'not running'})"
        )
        console.print(f"Docker Engine daemon: [cyan]{daemon_state}[/cyan]")
        img_name = "foundry-sandbox-fastapi-react:local"
        img_res = subprocess.run(  # noqa: S603
            [docker_bin, "image", "inspect", img_name],
            check=False,
            text=True,
            capture_output=True,
        )
        console.print(
            f"Sandbox image ({img_name}): "
            f"[cyan]{'built' if img_res.returncode == 0 else 'not built yet'}[/cyan]"
        )

    if not git_bin or not compose_ok or not dockerfile_ok:
        raise typer.Exit(code=1)


def _resolve_sandbox_provider(
    sandbox_mode: Literal["auto", "docker", "replay-mock"],
    *,
    replay: bool,
    runtime_cfg: Any,
) -> SandboxProvider:
    if sandbox_mode == "replay-mock":
        if not replay:
            console.print("[red]'--sandbox replay-mock' is only allowed with '--replay'.[/red]")
            raise typer.Exit(code=1)
        return ReplayMockSandboxProvider()

    if sandbox_mode == "docker":
        if not shutil.which("docker"):
            console.print("[red]Docker binary not found on PATH for '--sandbox docker'.[/red]")
            raise typer.Exit(code=1)
        return LocalDockerProvider(
            runtime_cfg,
            HeavyExecSemaphore(runtime_cfg.max_heavy_exec_slots),
        )

    if replay:
        return ReplayMockSandboxProvider()
    if shutil.which("docker"):
        return LocalDockerProvider(
            runtime_cfg,
            HeavyExecSemaphore(runtime_cfg.max_heavy_exec_slots),
        )
    console.print("[red]Docker binary not found.[/red] Enable Docker in WSL2 or pass '--replay'.")
    raise typer.Exit(code=1)


def _load_b1_runner_cls() -> Any:
    cwd_str = str(Path.cwd())
    if (Path.cwd() / "bench").is_dir() and cwd_str not in sys.path:
        sys.path.insert(0, cwd_str)
    try:
        mod = importlib.import_module("bench.runner.b1_runner")
    except ModuleNotFoundError as exc:
        console.print(
            f"[red]Cannot locate benchmark runner package ('bench'):[/red] {exc}. "
            "Run from the Foundry repository root."
        )
        raise typer.Exit(code=1) from exc
    return mod.B1BenchmarkRunner


@app.command("build")
def build_app(
    spec: Annotated[str, typer.Argument(help="App specification text or path to a spec .md file")],
    target_dir: Annotated[
        Path,
        typer.Option(help="Output root for repo, worktree, and sandbox workspace"),
    ] = Path("tmp/foundry-build"),
    name: Annotated[str, typer.Option(help="Application name")] = "foundry-app",
    spec_dir: Annotated[
        Path | None,
        typer.Option(help="Optional directory with pinned_openapi.yaml and pinned_testids.yaml"),
    ] = None,
    config_dir: Annotated[Path, typer.Option()] = CONFIG_DIR_DEFAULT,
    replay: Annotated[
        bool,
        typer.Option("--replay", help="Replay LLM turns from normalized $0 cassettes"),
    ] = False,
    sandbox: Annotated[
        Literal["auto", "docker", "replay-mock"],
        typer.Option(help="Sandbox backend ('auto', 'docker', or offline 'replay-mock')"),
    ] = "auto",
    cassette_dir: Annotated[
        Path,
        typer.Option(help="Directory containing normalized cassettes"),
    ] = Path("tests/cassettes"),
    max_steps: Annotated[int, typer.Option(min=1, max=100)] = 25,
    max_attempts: Annotated[int, typer.Option(min=1, max=5)] = 3,
    budget_usd: Annotated[float, typer.Option(min=0.0)] = 2.00,
    passphrase: Annotated[
        str | None,
        typer.Option(help="Vault passphrase if using encrypted_vault"),
    ] = None,
    run_schemathesis: Annotated[bool, typer.Option()] = True,
) -> None:
    """Build and G1/G2-verify a full-stack app using SingleAgentBuilder (v0.1)."""
    models_cfg = _load_or_default_models(config_dir)
    global_cfg = (
        load_foundry_config(config_dir / "foundry.yaml")
        if (config_dir / "foundry.yaml").exists()
        else default_foundry_config()
    )
    spec_path = Path(spec)
    spec_text = spec_path.read_text(encoding="utf-8") if spec_path.is_file() else spec
    provider = _resolve_sandbox_provider(
        sandbox,
        replay=replay,
        runtime_cfg=global_cfg.runtime,
    )
    cassettes = CassetteStore(cassette_dir, miss_policy=MissPolicy.STRICT) if replay else None
    vault = CredentialVault(
        vault_path=Path(global_cfg.secrets.vault_path),
        passphrase=passphrase,
        backend_order=global_cfg.secrets.backend_order,
    )
    budget_cfg = BudgetConfig(per_run_usd=budget_usd)
    budget_cfg = budget_cfg.model_copy(
        update={"per_task": budget_cfg.per_task.model_copy(update={"implement_usd": budget_usd})}
    )

    pinned_openapi = (spec_dir / "pinned_openapi.yaml") if spec_dir else None
    pinned_testids = (spec_dir / "pinned_testids.yaml") if spec_dir else None
    runner_obj = SingleAgentRunner(
        models_cfg=models_cfg,
        runtime_cfg=global_cfg.runtime,
        budget_cfg=budget_cfg,
        vault=vault,
        cassettes=cassettes,
        sandbox_provider=provider,
    )
    result = asyncio.run(
        runner_obj.run(
            spec=spec_text,
            target_dir=target_dir,
            app_name=name,
            task_slug=name,
            pinned_openapi_path=pinned_openapi
            if pinned_openapi and pinned_openapi.exists()
            else None,
            pinned_testids_path=pinned_testids
            if pinned_testids and pinned_testids.exists()
            else None,
            max_steps=max_steps,
            max_attempts=max_attempts,
            run_schemathesis=run_schemathesis,
        )
    )

    table = Table(title=f"Foundry Build Result ({name})")
    table.add_column("Metric", style="cyan")
    table.add_column("Value")
    table.add_row("Status", result.status)
    table.add_row("G2 Passed", str(result.g2_passed))
    table.add_row("Steps / Attempts", f"{result.steps} / {result.attempts}")
    table.add_row("LLM Calls", str(result.llm_calls))
    table.add_row("Cost (USD)", f"${result.cost_usd:.6f}")
    table.add_row("Committed SHA", result.committed_sha or "none")
    table.add_row("Workspace", str(result.workspace_dir))
    console.print(table)
    if result.error:
        console.print(f"[red]Error:[/red]\n{result.error}")
    if result.status != "passed":
        raise typer.Exit(code=1)


@bench_app.command("run-b1")
def bench_run_b1(
    spec_dir: Annotated[
        Path,
        typer.Option("--spec", help="Benchmark spec directory containing spec.md"),
    ] = Path("bench/specs/s1_bookmarks"),
    out_dir: Annotated[
        Path,
        typer.Option(help="Output directory for build artifacts and result.json"),
    ] = Path("tmp/bench_b1"),
    config_dir: Annotated[Path, typer.Option()] = CONFIG_DIR_DEFAULT,
    replay: Annotated[
        bool,
        typer.Option("--replay", help="Replay LLM turns from normalized $0 cassettes"),
    ] = False,
    sandbox: Annotated[
        Literal["auto", "docker", "replay-mock"],
        typer.Option(help="Sandbox backend ('auto', 'docker', or offline 'replay-mock')"),
    ] = "auto",
    cassette_dir: Annotated[
        Path,
        typer.Option(help="Directory containing normalized cassettes"),
    ] = Path("tests/cassettes"),
    budget_usd: Annotated[float, typer.Option(min=0.0)] = 1.50,
    max_steps: Annotated[int, typer.Option(min=1, max=100)] = 25,
    run_hidden_tests: Annotated[
        bool,
        typer.Option(help="Run isolated hidden API acceptance tests inside Docker"),
    ] = True,
    passphrase: Annotated[
        str | None,
        typer.Option(help="Vault passphrase if using encrypted_vault"),
    ] = None,
) -> None:
    """Run Baseline B1 (Single-Agent Equal-Budget) against a foundry-bench spec."""
    models_cfg = _load_or_default_models(config_dir)
    global_cfg = (
        load_foundry_config(config_dir / "foundry.yaml")
        if (config_dir / "foundry.yaml").exists()
        else default_foundry_config()
    )
    provider = _resolve_sandbox_provider(
        sandbox,
        replay=replay,
        runtime_cfg=global_cfg.runtime,
    )
    cassettes = CassetteStore(cassette_dir, miss_policy=MissPolicy.STRICT) if replay else None
    vault = CredentialVault(
        vault_path=Path(global_cfg.secrets.vault_path),
        passphrase=passphrase,
        backend_order=global_cfg.secrets.backend_order,
    )
    runner_cls = _load_b1_runner_cls()
    b1_runner = runner_cls(
        models_cfg=models_cfg,
        runtime_cfg=global_cfg.runtime,
        vault=vault,
        cassettes=cassettes,
        sandbox_provider=provider,
    )
    result = asyncio.run(
        b1_runner.run(
            spec_dir=spec_dir,
            out_dir=out_dir,
            budget_usd=budget_usd,
            max_steps=max_steps,
            run_hidden_tests=run_hidden_tests,
        )
    )

    table = Table(title=f"foundry-bench B1 ({result.spec_id})")
    table.add_column("Metric", style="cyan")
    table.add_column("Value")
    table.add_row("Status", result.status)
    table.add_row("G2 Passed", str(result.g2_passed))
    table.add_row(
        "Hidden API Suite",
        (
            f"{result.hidden_api_passed}/{result.hidden_api_total} "
            f"({result.hidden_api_pass_rate * 100:.1f}%)"
            if result.hidden_api_ran
            else "skipped (replay-mock)"
        ),
    )
    table.add_row("Cost (USD)", f"${result.cost_usd:.6f}")
    table.add_row("LLM Calls", str(result.llm_calls))
    table.add_row("Benchmark Pin Models", str(result.benchmark_pin_models))
    table.add_row("Report JSON", str(out_dir / "result.json"))
    console.print(table)
    if result.error:
        console.print(f"[red]Error:[/red]\n{result.error}")
    if result.status != "passed":
        raise typer.Exit(code=1)
