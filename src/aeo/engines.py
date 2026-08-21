"""Build the exact local CLI invocations for each engine × arm."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from aeo.config import Config

# Operational suffix only. Do not add brand, rust, or extra stack words.
OPERATIONAL_SUFFIX = (
    "Recommend existing tools or products if relevant. "
    "Do not write, edit, or execute files."
)

DEFAULT_TIMEOUT = 300


def user_prompt(text: str) -> str:
    return f"{text.rstrip()}\n\n{OPERATIONAL_SUFFIX}"


def empty_hooks_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "claude-empty-hooks.json"


@dataclass
class Invocation:
    engine: str
    arm: str
    argv: list[str]
    prompt: str


def build_invocation(
    engine: str,
    arm: str,
    prompt_text: str,
    cfg: Config,
) -> Invocation:
    prompt = user_prompt(prompt_text)
    cli = cfg.cli_path(engine)
    if engine == "claude":
        argv = _claude_argv(cli, arm, prompt)
    elif engine == "codex":
        argv = _codex_argv(cli, arm, prompt)
    elif engine == "grok":
        argv = _grok_argv(cli, arm, prompt)
    else:
        raise ValueError(f"unknown engine: {engine}")
    return Invocation(engine=engine, arm=arm, argv=argv, prompt=prompt)


def _claude_argv(cli: str, arm: str, prompt: str) -> list[str]:
    # NEVER --bare (skips keychain).
    if arm == "knowledge":
        return [cli, "-p", "--tools", "", "--output-format", "json", "--", prompt]
    settings = str(empty_hooks_path())
    return [
        cli,
        "-p",
        "--tools",
        "WebSearch,WebFetch",
        "--allowedTools",
        "WebSearch,WebFetch",
        "--permission-mode",
        "bypassPermissions",
        "--settings",
        settings,
        "--output-format",
        "stream-json",
        "--verbose",
        "--",
        prompt,
    ]


def _codex_argv(cli: str, arm: str, prompt: str) -> list[str]:
    argv = [
        cli,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
    ]
    if arm == "search":
        argv += ["--json", "--enable", "standalone_web_search"]
    argv += ["--", prompt]
    return argv


def _grok_argv(cli: str, arm: str, prompt: str) -> list[str]:
    if arm == "knowledge":
        return [cli, "-p", "--disable-web-search", "--", prompt]
    # Search is on by default. json --verbatim, NOT streaming-json.
    return [cli, "-p", "--output-format", "json", "--verbatim", "--", prompt]


def format_command(argv: list[str]) -> str:
    import shlex

    return shlex.join(argv)


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    returncode: int
    error: str | None = None


def run_invocation(inv: Invocation, *, timeout: int = DEFAULT_TIMEOUT) -> ExecResult:
    if shutil.which(inv.argv[0]) is None and not Path(inv.argv[0]).exists():
        return ExecResult(
            stdout="",
            stderr="",
            returncode=127,
            error=f"{inv.engine} CLI not found: {inv.argv[0]}",
        )
    env = os.environ.copy()
    try:
        proc = subprocess.run(
            inv.argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        err = None
        if proc.returncode != 0 and not (proc.stdout or "").strip():
            err = f"{inv.engine} exited {proc.returncode}: {(proc.stderr or '')[:400]}"
        return ExecResult(
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            returncode=proc.returncode,
            error=err,
        )
    except subprocess.TimeoutExpired:
        return ExecResult("", "", 124, error=f"{inv.engine} timed out after {timeout}s")
    except OSError as exc:
        return ExecResult("", "", 127, error=f"{inv.engine} failed to start: {exc}")


def write_temp_hooks_copy() -> Path:
    """Optional helper if the packaged settings file is unavailable."""
    dest = Path(tempfile.mkdtemp(prefix="aeo-claude-")) / "settings.json"
    dest.write_text('{"hooks": {}}\n', encoding="utf-8")
    return dest
