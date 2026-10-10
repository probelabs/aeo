"""Build the exact local CLI invocations for each engine × arm."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aeo import isolation
from aeo.config import Config

# Operational suffix only. Do not add brand, rust, or extra stack words.
OPERATIONAL_SUFFIX = (
    "Recommend existing tools or products if relevant. "
    "Do not write, edit, execute, or read files from disk. "
    "Do not inspect the working directory or parent folders."
)

DEFAULT_TIMEOUT = 300
GROK_FILE_TOOLS = "read,write,edit,bash,glob,grep,ls"


def user_prompt(text: str) -> str:
    return f"{text.rstrip()}\n\n{OPERATIONAL_SUFFIX}"


def empty_hooks_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "claude-empty-hooks.json"


def isolate_cwd() -> Path:
    """Empty dir whose parent is NOT the AEO/brand tree.

    Grok/Codex will list `.` and `..`. A cwd of ~/.aeo/scratch lets the
    model read protocol.json, keywords, and the playbook in the parent.
    /tmp/aeo-isolate-* only shows other temp dirs.
    """
    root = Path(os.environ.get("AEO_ISOLATE_ROOT") or tempfile.gettempdir())
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="aeo-isolate-", dir=str(root)))


@dataclass
class Invocation:
    engine: str
    arm: str
    argv: list[str]
    prompt: str
    cwd: Path | None = None
    # Fresh engine home (no identity, memory, instructions, MCP or hooks) per call.
    isolate: bool = True
    isolation_settings: dict[str, Any] = field(default_factory=dict)


def build_invocation(
    engine: str,
    arm: str,
    prompt_text: str,
    cfg: Config,
    *,
    make_cwd: bool = True,
    raw_prompt: bool = False,
) -> Invocation:
    prompt = prompt_text if raw_prompt else user_prompt(prompt_text)
    cli = cfg.cli_path(engine)
    isolated = isolate_cwd() if make_cwd else Path(tempfile.gettempdir()) / "aeo-isolate-XXXX"
    if engine == "claude":
        argv = _claude_argv(cli, arm, prompt)
    elif engine == "codex":
        argv = _codex_argv(cli, arm, prompt)
    elif engine == "grok":
        argv = _grok_argv(cli, arm, prompt, isolated)
    else:
        raise ValueError(f"unknown engine: {engine}")
    return Invocation(engine=engine, arm=arm, argv=argv, prompt=prompt, cwd=isolated)


def _claude_argv(cli: str, arm: str, prompt: str) -> list[str]:
    # No --bare with subscription auth: it skips the keychain login. Isolation
    # instead comes from an empty HOME per call (aeo.isolation) plus these flags;
    # aeo.isolation adds --bare itself when ANTHROPIC_API_KEY is configured.
    # stream-json on both arms so tool availability and use are always recorded.
    common = list(isolation.CLAUDE_ISOLATION_FLAGS)
    if arm == "knowledge":
        return [cli, "-p", "--tools", "", *common, "--output-format", "stream-json", "--verbose", "--", prompt]
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
        *common,
        "--output-format",
        "stream-json",
        "--verbose",
        "--",
        prompt,
    ]


# Codex knowledge arm: web search explicitly off. `web_search = "disabled"` is the
# config switch for the built-in web search tool; the standalone search feature
# is disabled too. --json on both arms so every tool call is recorded.
CODEX_NO_SEARCH = ["-c", 'web_search="disabled"', "--disable", "standalone_web_search"]
CODEX_SEARCH = ["--enable", "standalone_web_search"]


def _codex_argv(cli: str, arm: str, prompt: str) -> list[str]:
    argv = [
        cli,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--json",
    ]
    argv += CODEX_SEARCH if arm == "search" else CODEX_NO_SEARCH
    argv += ["--", prompt]
    return argv


def _grok_argv(cli: str, arm: str, prompt: str, cwd: Path) -> list[str]:
    # -p/--single consumes the next argument as the prompt. Flags must come first.
    # strict: read CWD + system paths only (not ~/.aeo). On macOS child network
    # still works so the search arm can use web_search.
    # Default strict. Stock grok refuses strict when /var/run/docker.sock is a
    # symlink (Docker Desktop). Override with GROK_SANDBOX=workspace when needed.
    # GROK_HOME is a fresh home per call holding only auth (aeo.isolation).
    sandbox = os.environ.get("GROK_SANDBOX") or "strict"
    argv = [
        cli,
        "--cwd",
        str(cwd),
        "--no-memory",
        "--sandbox",
        sandbox,
        "--disallowed-tools",
        GROK_FILE_TOOLS,
        "--verbatim",
    ]
    if arm == "knowledge":
        argv += ["--disable-web-search"]
    argv += ["--output-format", "json", "-p", prompt]
    return argv


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
    user_sock = Path.home() / ".docker/run/docker.sock"
    if user_sock.exists() and "DOCKER_HOST" not in env:
        env["DOCKER_HOST"] = f"unix://{user_sock}"
    iso = isolation.prepare(inv.engine, env) if inv.isolate else None
    argv = list(inv.argv)
    if iso is not None:
        env = iso.env
        argv = argv[:1] + [a for a in iso.extra_argv if a not in argv] + argv[1:]
        inv.isolation_settings = dict(iso.settings)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(inv.cwd) if inv.cwd else None,
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
    finally:
        if iso is not None:
            iso.cleanup()
        if inv.cwd is not None and inv.cwd.name.startswith("aeo-isolate-"):
            shutil.rmtree(inv.cwd, ignore_errors=True)


def cli_version(cli: str, timeout: int = 20) -> str | None:
    """`<cli> --version`, first line; None when the CLI is missing or fails."""
    if shutil.which(cli) is None and not Path(cli).exists():
        return None
    try:
        proc = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (proc.stdout or proc.stderr or "").strip().splitlines()
    return out[0].strip() if out else None


def argv_template(engine: str, arm: str, cfg: Config) -> list[str]:
    """The flags used for an engine × arm, with the question replaced by <prompt>."""
    inv = build_invocation(engine, arm, "<prompt>", cfg, make_cwd=False)
    return [("<prompt>" if a == inv.prompt else a) for a in inv.argv]


def write_temp_hooks_copy() -> Path:
    """Optional helper if the packaged settings file is unavailable."""
    dest = Path(tempfile.mkdtemp(prefix="aeo-claude-")) / "settings.json"
    dest.write_text('{"hooks": {}}\n', encoding="utf-8")
    return dest
