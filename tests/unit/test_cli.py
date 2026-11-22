"""CLI smoke tests running offline at $0 API cost."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from foundry.cli.main import app

runner = CliRunner()


def test_cli_help() -> None:
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "Foundry" in res.stdout


def test_cli_init_list_assign_and_replay_test(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    proj_dir = tmp_path / "project"

    init_res = runner.invoke(
        app,
        [
            "init",
            "--config-dir",
            str(cfg_dir),
            "--project-dir",
            str(proj_dir),
            "--name",
            "bookmarks",
        ],
    )
    assert init_res.exit_code == 0
    assert (cfg_dir / "foundry.yaml").exists()
    assert (cfg_dir / "models.yaml").exists()
    assert (proj_dir / ".foundry" / "project.yaml").exists()

    list_res = runner.invoke(app, ["models", "list", "--config-dir", str(cfg_dir)])
    assert list_res.exit_code == 0
    assert "openai_compat" in list_res.stdout
    assert "google_genai" in list_res.stdout
    assert "anthropic" in list_res.stdout
    assert "litellm" not in list_res.stdout.lower()

    assign_res = runner.invoke(
        app,
        ["models", "assign", "implementer", "@coder_deepseek", "--config-dir", str(cfg_dir)],
    )
    assert assign_res.exit_code == 0
    assert "@coder_deepseek" in (cfg_dir / "models.yaml").read_text(encoding="utf-8")

    test_res = runner.invoke(
        app,
        [
            "models",
            "test",
            "--replay",
            "--cassette-dir",
            "tests/cassettes",
            "--config-dir",
            str(cfg_dir),
        ],
    )
    assert test_res.exit_code == 0
    assert "replayed=True" in test_res.stdout
    assert "cost_usd=$0.000000" in test_res.stdout


def test_cli_stack_scaffold_and_sandbox_check(tmp_path: Path) -> None:
    app_dir = tmp_path / "scaffolded"
    res = runner.invoke(app, ["stack", "scaffold", str(app_dir), "--name", "demo-app"])
    assert res.exit_code == 0
    assert (app_dir / "backend" / "app" / "main.py").exists()
    assert (app_dir / "frontend" / "src" / "app" / "AppLayout.tsx").exists()

    chk = runner.invoke(app, ["sandbox", "check"])
    # In WSL2 git & infra files exist; docker binary may or may not be present yet
    assert "Daemon git binary:" in chk.stdout
    assert "infra/docker-compose.yml: present" in chk.stdout
    assert "infra/sandbox.Dockerfile: present" in chk.stdout


def test_cli_build_and_bench_b1_replay_offline(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "cfg"
    proj_dir = tmp_path / "proj"
    assert (
        runner.invoke(
            app,
            ["init", "--config-dir", str(cfg_dir), "--project-dir", str(proj_dir), "--name", "s1"],
        ).exit_code
        == 0
    )

    build_dir = tmp_path / "cli_build"
    build_res = runner.invoke(
        app,
        [
            "build",
            "bench/specs/s1_bookmarks/spec.md",
            "--name",
            "s1_bookmarks",
            "--spec-dir",
            "bench/specs/s1_bookmarks",
            "--target-dir",
            str(build_dir),
            "--config-dir",
            str(cfg_dir),
            "--replay",
            "--sandbox",
            "replay-mock",
            "--cassette-dir",
            "tests/cassettes",
        ],
    )
    assert build_res.exit_code == 0, build_res.stdout
    assert "passed" in build_res.stdout

    # replay-mock without --replay must be rejected
    bad_res = runner.invoke(
        app,
        [
            "build",
            "Build something",
            "--sandbox",
            "replay-mock",
            "--config-dir",
            str(cfg_dir),
        ],
    )
    assert bad_res.exit_code != 0

    bench_dir = tmp_path / "cli_bench"
    bench_res = runner.invoke(
        app,
        [
            "bench",
            "run-b1",
            "--spec",
            "bench/specs/s1_bookmarks",
            "--out-dir",
            str(bench_dir),
            "--config-dir",
            str(cfg_dir),
            "--replay",
            "--sandbox",
            "replay-mock",
            "--cassette-dir",
            "tests/cassettes",
        ],
    )
    assert bench_res.exit_code == 0, bench_res.stdout
    assert (bench_dir / "result.json").is_file()


def test_cli_run_start_status_and_resume_offline(tmp_path: Path) -> None:
    import re

    cfg_dir = tmp_path / "cfg_run"
    proj_dir = tmp_path / "proj_run"
    state_file = tmp_path / "bb_state.json"
    assert (
        runner.invoke(
            app,
            ["init", "--config-dir", str(cfg_dir), "--project-dir", str(proj_dir), "--name", "s1"],
        ).exit_code
        == 0
    )

    start_res = runner.invoke(
        app,
        [
            "run",
            "start",
            "bench/specs/s1_bookmarks/spec.md",
            "--name",
            "s1_bookmarks",
            "--spec-dir",
            "bench/specs/s1_bookmarks",
            "--autonomy",
            "autopilot",
            "--config-dir",
            str(cfg_dir),
            "--replay",
            "--cassette-dir",
            "tests/cassettes",
            "--state-file",
            str(state_file),
        ],
    )
    assert start_res.exit_code == 0, start_res.stdout
    assert "completed" in start_res.stdout
    match = re.search(r"(run_[0-9a-z]+)", start_res.stdout)
    assert match is not None
    run_id = match.group(1)

    status_res = runner.invoke(
        app,
        ["run", "status", run_id, "--state-file", str(state_file)],
    )
    assert status_res.exit_code == 0, status_res.stdout
    assert "4/4" in status_res.stdout
    assert "MERGED" in status_res.stdout
