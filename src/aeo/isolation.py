"""Run each engine CLI with no user identity, memory, instructions, MCP or hooks.

Every cell gets a fresh, empty engine home that holds only what the CLI needs to
authenticate, plus an empty working directory (see engines.isolate_cwd):

- Claude: ``HOME`` points at an empty temp dir, so ``~/.claude.json`` (cached
  account profile and email), ``~/.claude/CLAUDE.md``, settings, hooks, plugins,
  skills, auto-memory and project history are all absent. ``CLAUDE_CONFIG_DIR``
  is unset so the CLI still reads its normal macOS keychain login
  ("Claude Code-credentials"); a token refresh writes back to that same item, so
  the user's own Claude Code login is never forked. ``--bare`` is not used for
  subscription auth because it skips the keychain. When ``ANTHROPIC_API_KEY``
  is set, the home is an isolated ``CLAUDE_CONFIG_DIR`` plus ``--bare`` instead.
  ``CLAUDE_CODE_OAUTH_TOKEN`` (a ``claude setup-token`` token) also works with an
  isolated ``CLAUDE_CONFIG_DIR``.
- Codex: ``CODEX_HOME`` is an empty temp dir with ``auth.json`` symlinked to the
  real login and a config.toml that turns off apps, plugins, memories and hooks.
  No user config.toml (MCP servers, personality, model overrides), no AGENTS.md,
  no skills, no history. If Codex refreshes its token it replaces the symlink
  with a file; that newer login is copied back so the user's own Codex stays
  signed in.
- Grok: ``GROK_HOME`` is an empty temp dir with ``auth.json`` symlinked.

Nothing here prints or records a secret. ``settings`` records only which auth
method was used and which isolation switches were applied.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Flags added to every Claude call (both arms).
CLAUDE_ISOLATION_FLAGS = ["--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"]

CODEX_ISOLATED_CONFIG = """# written by aeo.isolation: no user config, MCP, apps, plugins, memories or hooks
[features]
apps = false
plugins = false
remote_plugin = false
memories = false
hooks = false
"""

ISOLATION_VERSION = "aeo-isolation-v1"


def _root() -> Path:
    root = Path(os.environ.get("AEO_ISOLATE_ROOT") or tempfile.gettempdir())
    root.mkdir(parents=True, exist_ok=True)
    return root


def _home_dir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(_root())))


def _real_home() -> Path:
    return Path(os.environ.get("AEO_REAL_HOME") or Path.home())


@dataclass
class IsolatedEnv:
    engine: str
    env: dict[str, str]
    extra_argv: list[str] = field(default_factory=list)
    home: Path | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    _after: list[Callable[[], None]] = field(default_factory=list)

    def cleanup(self) -> None:
        for fn in self._after:
            try:
                fn()
            except OSError:
                pass
        if self.home is not None and self.home.exists():
            shutil.rmtree(self.home, ignore_errors=True)


# Fields Claude Code needs to skip its startup profile fetch. Name, email and
# organization name are left out on purpose: Claude Code puts the account email
# into every session ("The user's email address is ...").
_PROFILE_KEEP = (
    "accountUuid", "organizationUuid", "billingType", "accountCreatedAt", "subscriptionCreatedAt",
    "hasExtraUsageEnabled", "organizationType", "organizationRateLimitTier", "userRateLimitTier",
    "seatTier", "organizationRole", "workspaceRole", "ccOnboardingFlags", "claudeCodeTrialEndsAt",
    "claudeCodeTrialDurationDays",
)


def _link_keychains(home: Path) -> None:
    """macOS finds the login keychain through $HOME; link only the keychain folder."""
    real = _real_home() / "Library" / "Keychains"
    if real.is_dir():
        (home / "Library").mkdir(parents=True, exist_ok=True)
        (home / "Library" / "Keychains").symlink_to(real)


def _seed_claude_profile(home: Path) -> str:
    """Write a ~/.claude.json holding the account profile minus name and email.

    With a recent, complete profile Claude Code skips the profile fetch at
    startup, so the session starts without the account email. It may refetch in
    the background and write the email back; every answer gets a fresh HOME, so
    that copy is never read. The identity canary checks the result.
    """
    try:
        real = json.loads((_real_home() / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        real = {}
    acct = real.get("oauthAccount") if isinstance(real, dict) else None
    doc: dict[str, Any] = {"hasCompletedOnboarding": True}
    status = "no saved profile found; Claude Code may fetch it (canary checks)"
    if isinstance(acct, dict) and acct:
        seeded = {k: acct[k] for k in _PROFILE_KEEP if k in acct}
        seeded["emailAddress"] = ""
        seeded["profileFetchedAt"] = int(time.time() * 1000)
        doc["oauthAccount"] = seeded
        status = "seeded without name, email or organization name"
    path = home / ".claude.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    os.chmod(path, 0o600)
    return status


def claude_auth_mode(env: dict[str, str]) -> str:
    if (env.get("ANTHROPIC_API_KEY") or "").strip():
        return "api_key"
    if (env.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip():
        return "oauth_token_env"
    return "keychain"


def _claude(env: dict[str, str]) -> IsolatedEnv:
    home = _home_dir("aeo-claude-home-")
    mode = claude_auth_mode(env)
    extra: list[str] = []
    env = dict(env)
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    profile = "not seeded"
    if mode == "keychain":
        # Empty HOME: no ~/.claude.json, ~/.claude/*, CLAUDE.md, memory or MCP.
        # CLAUDE_CONFIG_DIR unset so the keychain item name stays the default one.
        env.pop("CLAUDE_CONFIG_DIR", None)
        env["HOME"] = str(home)
        _link_keychains(home)
        profile = _seed_claude_profile(home)
        config_dir = home / ".claude"
    else:
        config_dir = home / ".claude"
        config_dir.mkdir(parents=True, exist_ok=True)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        env["HOME"] = str(home)
        if mode == "api_key":
            extra.append("--bare")
    settings = {
        "isolation": ISOLATION_VERSION,
        "auth": {"api_key": "ANTHROPIC_API_KEY", "oauth_token_env": "CLAUDE_CODE_OAUTH_TOKEN",
                 "keychain": "macOS keychain login (Claude Code-credentials)"}[mode],
        "home": "fresh empty HOME per answer" if mode == "keychain" else "fresh CLAUDE_CONFIG_DIR per answer",
        "user_config": False,
        "claude_md": False,
        "auto_memory": False,
        "mcp": False,
        "hooks": False,
        "plugins_skills": False,
        "session_persistence": False,
        "flags": CLAUDE_ISOLATION_FLAGS + extra,
        "account_profile": profile,
    }
    return IsolatedEnv("claude", env, extra_argv=extra, home=home, settings=settings)


def _codex_auth_source(env: dict[str, str]) -> Path:
    src = env.get("AEO_CODEX_AUTH_HOME") or env.get("CODEX_HOME") or str(_real_home() / ".codex")
    return Path(src) / "auth.json"


def _sync_back(tmp_auth: Path, src_auth: Path) -> None:
    """Codex replaced the symlink with a refreshed login: keep the user's copy current."""
    if tmp_auth.is_symlink() or not tmp_auth.is_file() or not src_auth.exists():
        return
    try:
        new = json.loads(tmp_auth.read_text(encoding="utf-8"))
        old = json.loads(src_auth.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if new == old or not isinstance(new, dict):
        return
    if str(new.get("last_refresh") or "") <= str(old.get("last_refresh") or ""):
        return
    staged = src_auth.with_name(src_auth.name + ".aeo-sync")
    shutil.copyfile(tmp_auth, staged)
    os.chmod(staged, 0o600)
    os.replace(staged, src_auth)


def _codex(env: dict[str, str]) -> IsolatedEnv:
    home = _home_dir("aeo-codex-home-")
    src = _codex_auth_source(env)
    env = dict(env)
    env["CODEX_HOME"] = str(home)
    (home / "config.toml").write_text(CODEX_ISOLATED_CONFIG, encoding="utf-8")
    tmp_auth = home / "auth.json"
    auth = "none found"
    if src.exists():
        tmp_auth.symlink_to(src)
        auth = "ChatGPT/API login (auth.json, symlinked)"
    elif (env.get("OPENAI_API_KEY") or "").strip():
        auth = "OPENAI_API_KEY"
    settings = {
        "isolation": ISOLATION_VERSION,
        "auth": auth,
        "home": "fresh empty CODEX_HOME per answer",
        "user_config": False,
        "agents_md": False,
        "memories": False,
        "mcp": False,
        "apps_plugins": False,
        "hooks": False,
        "session_persistence": False,
    }
    iso = IsolatedEnv("codex", env, home=home, settings=settings)
    iso._after.append(lambda: _sync_back(tmp_auth, src))
    return iso


def _grok(env: dict[str, str]) -> IsolatedEnv:
    home = _home_dir("aeo-grok-home-")
    src_home = Path(env.get("AEO_GROK_AUTH_HOME") or env.get("GROK_HOME") or str(_real_home() / ".grok-aeo-nomcp"))
    if not (src_home / "auth.json").exists():
        src_home = _real_home() / ".grok"
    env = dict(env)
    env["GROK_HOME"] = str(home)
    auth = "none found"
    if (src_home / "auth.json").exists():
        (home / "auth.json").symlink_to(src_home / "auth.json")
        auth = "grok login (auth.json, symlinked)"
    settings = {
        "isolation": ISOLATION_VERSION,
        "auth": auth,
        "home": "fresh empty GROK_HOME per answer",
        "user_config": False,
        "memory": False,
        "mcp": False,
        "file_tools": False,
    }
    return IsolatedEnv("grok", env, home=home, settings=settings)


def prepare(engine: str, base_env: dict[str, str] | None = None) -> IsolatedEnv:
    env = dict(os.environ if base_env is None else base_env)
    if engine == "claude":
        return _claude(env)
    if engine == "codex":
        return _codex(env)
    if engine == "grok":
        return _grok(env)
    raise ValueError(f"unknown engine: {engine}")


def describe(engine: str, base_env: dict[str, str] | None = None) -> dict[str, Any]:
    """Isolation settings for run metadata, without leaving a temp home behind."""
    iso = prepare(engine, base_env)
    try:
        return dict(iso.settings)
    finally:
        iso.cleanup()
