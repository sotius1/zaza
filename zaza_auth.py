"""
Zaza Auth — gating layer for Agent ZAZA.

Login uses the same email + password as zaza.net.pl. Plan EPIC is required
to actually run the agent; without it the CLI exits with an upgrade message.

Public API:
    require_epic()     # top-of-CLI guard. Logs in if needed; exits if plan != EPIC.
    login()            # explicit: 'agent-zaza login'
    logout()           # explicit: 'agent-zaza logout'
    me()               # explicit: 'agent-zaza me'
    api_call(...)      # helper: authenticated POST/GET against /api/agent
"""

from __future__ import annotations

import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

VERSION = "0.5.0"
API_BASE = os.environ.get("ZAZA_API_BASE", "https://www.zaza.net.pl")
CONFIG_DIR = Path(
    os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
) / "agent-zaza"
CRED_PATH = CONFIG_DIR / "credentials.json"

# ANSI colors (lime accent, no chalk dep)
_LIME = "\033[38;2;202;255;51m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RED = "\033[31m"
_RST = "\033[0m"


def _is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _c(color: str, s: str) -> str:
    return f"{color}{s}{_RST}" if _is_tty() else s


BANNER = f"""
{_LIME}    ╔═══════════════════════════════════════╗
    ║   {_BOLD}AGENT ZAZA{_RST}{_LIME} · v{VERSION}{" " * (20 - len(VERSION))}║
    ║   {_DIM}studio inżynierii AI · zaza.net.pl{_RST}{_LIME} ║
    ╚═══════════════════════════════════════╝{_RST}
"""


# ----------------------------------------------------------------------
# Credentials
# ----------------------------------------------------------------------
def _load_creds() -> Optional[dict]:
    try:
        return json.loads(CRED_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_creds(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    CRED_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        CRED_PATH.chmod(0o600)
    except OSError:
        pass


def _clear_creds() -> None:
    try:
        CRED_PATH.unlink()
    except FileNotFoundError:
        pass


# ----------------------------------------------------------------------
# Provider auto-config: write OPENAI_BASE_URL + key into the agent's .env
# so the OpenAI-compatible client routes every chat through zaza.net.pl.
# Block is delimited by markers so we can update it idempotently without
# touching the user's other env vars.
# ----------------------------------------------------------------------
PROVIDER_ENV_BEGIN = "# >>> agent-zaza provider (auto) >>>"
PROVIDER_ENV_END = "# <<< agent-zaza provider (auto) <<<"


def _zaza_data_dir() -> Path:
    """Resolve the data directory without importing zaza_constants (avoids
    pulling in the heavy CLI just to do a login)."""
    override = os.environ.get("ZAZA_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".agent-zaza" / "data"


def _capture_desktop_env() -> dict:
    """Snapshot the current login shell's desktop env so child agent processes
    can launch GUI apps. Without this `subprocess.Popen(['google-chrome'])`
    fails with 'Missing X server or $DISPLAY'.

    These vars are best-effort — if any are absent (headless, SSH without -X,
    container) we just skip them and fall back to defaults at launch time."""
    keys = (
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "XDG_SESSION_TYPE",
        "XDG_CURRENT_DESKTOP",
        "XDG_DATA_DIRS",
        "XDG_CONFIG_DIRS",
        "DBUS_SESSION_BUS_ADDRESS",
        "GNOME_TERMINAL_SCREEN",
        "DESKTOP_SESSION",
        "USER",
        "HOME",
        "LANG",
        "LC_ALL",
    )
    out = {}
    for k in keys:
        v = os.environ.get(k)
        if v:
            out[k] = v
    # Sane fallback so models that test for DISPLAY don't fall back to
    # "I run in a sandbox without GUI access" — most desktop installs use :0.
    out.setdefault("DISPLAY", ":0")
    return out


def _write_provider_env(token: str) -> None:
    data_dir = _zaza_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    env_path = data_dir / ".env"
    desktop_env = _capture_desktop_env()
    desktop_lines = "\n".join(f"{k}={v}" for k, v in desktop_env.items())
    block = (
        f"{PROVIDER_ENV_BEGIN}\n"
        f"OPENAI_API_KEY={token}\n"
        f"OPENAI_BASE_URL={API_BASE}/api/agent/v1\n"
        f"ZAZA_DEFAULT_MODEL=zaza-1.1\n"
        f"{desktop_lines}\n"
        f"{PROVIDER_ENV_END}\n"
    )
    existing = ""
    if env_path.exists():
        try:
            existing = env_path.read_text()
        except OSError:
            existing = ""
    # Strip any prior auto block (idempotent re-login)
    if PROVIDER_ENV_BEGIN in existing and PROVIDER_ENV_END in existing:
        before, _, rest = existing.partition(PROVIDER_ENV_BEGIN)
        _, _, after = rest.partition(PROVIDER_ENV_END)
        existing = (before.rstrip() + "\n" + after.lstrip()).strip() + "\n"
        if existing.strip() == "":
            existing = ""
    new_content = (existing.rstrip() + "\n\n" + block).lstrip("\n")
    try:
        env_path.write_text(new_content)
        env_path.chmod(0o600)
    except OSError:
        pass

    _ensure_default_config(data_dir)


def _ensure_default_config(data_dir: Path) -> None:
    """Drop a sensible config.yaml on first login so the agent can dispatch
    chat to zaza.net.pl without the user touching ``zaza setup``. Safe to
    run on existing installs — only writes if no model.provider is already
    pinned to a custom endpoint, otherwise leaves the user's config alone.
    """
    cfg_path = data_dir / "config.yaml"
    # Only seed when no config exists, or the existing config is the legacy
    # StepFun-direct preset shipped with the upstream fork. Anything else is
    # treated as user-curated and left untouched.
    if cfg_path.exists():
        try:
            content = cfg_path.read_text()
        except OSError:
            return
        is_legacy_stepfun = (
            "custom-api-stepfun-ai" in content
            or "step-1-256k" in content
            or "step-3.5-flash" in content
        )
        already_zaza = (
            "provider: custom" in content
            and "zaza.net.pl/api/agent/v1" in content
        )
        if already_zaza or not is_legacy_stepfun:
            return
    default_yaml = (
        "model:\n"
        "  default: zaza-1.1\n"
        "  provider: custom\n"
        f"  base_url: {API_BASE}/api/agent/v1\n"
        "  api_key: \"\"\n"
        "  context_length: 128000\n"
        "  max_tokens: 16000\n"
        "providers: {}\n"
        "fallback_providers: []\n"
        "fallback_model: null\n"
        "credential_pool_strategies: {}\n"
        # Rich, opinionated default toolset — covers ~90% of agentic work\n"
        # (file editing, shell, browser automation, code exec, memory recall,
        # web search, in-session todo, clarification turn). Users can prune
        # via `zaza config set toolsets [...]` if they want lighter footprint.
        # NOTE: 'browser' (legacy abstracted CDP wrapper) is intentionally NOT
        # in the default — browser_harness gives the model raw CDP and is
        # 10x more capable for non-trivial sites.
        "toolsets:\n"
        "  - terminal\n"
        "  - file\n"
        "  - browser_harness\n"
        "  - code_execution\n"
        "  - memory\n"
        "  - web\n"
        "  - todo\n"
        "  - clarify\n"
        "  - delegation\n"
        "  - search\n"
        "  - mcp\n"
        # MCP servers — pre-wired on first login so the agent has Serena
        # (LSP-driven code intel), Chrome DevTools (live browser harness),
        # Sequential Thinking, and persistent memory available out of the
        # box. uvx/npx must be in PATH; install.sh sets these up.
        "mcp_servers:\n"
        "  serena:\n"
        "    command: uvx\n"
        "    args:\n"
        "      - \"--from\"\n"
        "      - \"git+https://github.com/oraios/serena\"\n"
        "      - \"serena\"\n"
        "      - \"start-mcp-server\"\n"
        "    description: \"Serena — LSP-backed code intelligence. Use BEFORE grep for symbol-level navigation and surgical edits.\"\n"
        "    enabled: true\n"
        "  chrome-devtools:\n"
        "    command: npx\n"
        "    args:\n"
        "      - \"-y\"\n"
        "      - \"chrome-devtools-mcp@latest\"\n"
        "    description: \"Chrome DevTools Protocol — live browser automation, console, network, screenshots.\"\n"
        "    enabled: true\n"
        "  sequential-thinking:\n"
        "    command: npx\n"
        "    args:\n"
        "      - \"-y\"\n"
        "      - \"@modelcontextprotocol/server-sequential-thinking\"\n"
        "    description: \"Sequential Thinking — structured multi-step reasoning.\"\n"
        "    enabled: true\n"
        "  memory:\n"
        "    command: npx\n"
        "    args:\n"
        "      - \"-y\"\n"
        "      - \"@modelcontextprotocol/server-memory\"\n"
        "    description: \"Persistent knowledge graph across sessions.\"\n"
        "    enabled: true\n"
        "agent:\n"
        "  max_turns: 90\n"
        "  api_max_retries: 3\n"
        "  reasoning_effort: high\n"
        "  verbose: 'off'\n"
        "display:\n"
        "  compact: false\n"
        "  resume_display: full\n"
        "  streaming: true\n"
        "privacy:\n"
        "  redact_pii: false\n"
        "security:\n"
        "  # Tirith pattern-matching scanner produces false positives on ordinary\n"
        "  # commands when the upstream binary isn't available. Disabled here so\n"
        "  # the agent can actually run shell commands; re-enable once you've\n"
        "  # installed tirith binary if you want active scanning.\n"
        "  tirith_enabled: false\n"
        "approvals:\n"
        "  # Default policy: ask for non-trivial commands, but auto-approve\n"
        "  # read-only inspection + GUI launchers so the agent feels responsive.\n"
        "  mode: smart\n"
        "  allowlist:\n"
        "    - ls\n"
        "    - pwd\n"
        "    - cd\n"
        "    - which\n"
        "    - whereis\n"
        "    - echo\n"
        "    - cat\n"
        "    - head\n"
        "    - tail\n"
        "    - wc\n"
        "    - find\n"
        "    - grep\n"
        "    - rg\n"
        "    - fd\n"
        "    - env\n"
        "    - printenv\n"
        "    - df\n"
        "    - du\n"
        "    - free\n"
        "    - uptime\n"
        "    - uname\n"
        "    - hostname\n"
        "    - id\n"
        "    - stat\n"
        "    - file\n"
        "    - tree\n"
        "    - xdg-open\n"
        "    - google-chrome\n"
        "    - google-chrome-stable\n"
        "    - chromium\n"
        "    - chromium-browser\n"
        "    - firefox\n"
        "    - code\n"
        "    - subl\n"
        "    - gnome-terminal\n"
        "    - xterm\n"
        "tips:\n"
        "  # One-time helper messages — silence the noisy 'OpenClaw migration' tip\n"
        "  # since most fresh installs don't have an OpenClaw history.\n"
        "  show_openclaw_migration: false\n"
    )
    try:
        cfg_path.write_text(default_yaml)
    except OSError:
        pass


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------
def _http(
    method: str,
    path: str,
    body: Optional[dict] = None,
    bearer: Optional[str] = None,
) -> tuple[int, dict]:
    url = API_BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"agent-zaza/{VERSION}",
    }
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read()
            try:
                return resp.status, json.loads(payload.decode("utf-8"))
            except json.JSONDecodeError:
                return resp.status, {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except urllib.error.URLError as e:
        print(_c(_RED, f"Błąd sieci: {e.reason}"), file=sys.stderr)
        return 0, {"error": "network", "detail": str(e.reason)}


def api_call(method: str, action: str, **kwargs) -> tuple[int, dict]:
    """Authenticated helper for backend actions other than auth.
    Bearer is auto-loaded from credentials. Raises SystemExit if no creds."""
    creds = _load_creds()
    if not creds:
        print(_c(_RED, "Nie zalogowano. Uruchom: agent-zaza login"), file=sys.stderr)
        raise SystemExit(1)
    body = kwargs.copy()
    body["action"] = action
    return _http(method, "/api/agent", body=body, bearer=creds["token"])


# ----------------------------------------------------------------------
# Login — direct credential exchange against zaza.net.pl
# (same email + password you use to sign in on the website).
# ----------------------------------------------------------------------
_ERR_MAP = {
    "invalid_credentials": "Nieprawidłowy email lub hasło.",
    "email_not_confirmed": "Email nie potwierdzony — sprawdź skrzynkę po rejestracji.",
    "rate_limit": "Za dużo prób logowania. Spróbuj za kilka minut.",
    "missing_credentials": "Email i hasło są wymagane.",
    "invalid_email": "Email ma niepoprawny format.",
    "password_too_long": "Hasło zbyt długie (max 200 znaków).",
}


def login() -> dict:
    print(BANNER)
    print(_c(_DIM, "Login do Agent ZAZA — użyj credential z zaza.net.pl"))
    print()
    try:
        email = input("  " + _c(_LIME, "Email:    ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(_c(_DIM, "\n  przerwano."))
        raise SystemExit(130)
    if not email:
        print(_c(_RED, "  Brak emaila."), file=sys.stderr)
        raise SystemExit(1)
    try:
        password = getpass.getpass("  " + _c(_LIME, "Hasło:    "))
    except (EOFError, KeyboardInterrupt):
        print(_c(_DIM, "\n  przerwano."))
        raise SystemExit(130)
    if not password:
        print(_c(_RED, "  Brak hasła."), file=sys.stderr)
        raise SystemExit(1)

    print(_c(_DIM, "  Loguję..."))
    code, data = _http(
        "POST",
        "/api/agent",
        body={"action": "password-login", "email": email, "password": password},
    )
    if code != 200 or not data.get("ok"):
        msg = _ERR_MAP.get(
            data.get("error"), "Błąd: " + str(data.get("error", code))
        )
        print(_c(_RED, "  " + msg), file=sys.stderr)
        raise SystemExit(1)

    creds = {
        "token": data["agent_token"],
        "email": data["user"]["email"],
        "api_base": API_BASE,
        "savedAt": int(time.time() * 1000),
    }
    _save_creds(creds)
    _write_provider_env(creds["token"])

    plan_tier = (data.get("plan") or {}).get("tier", "free")
    print()
    print(_c(_LIME, f"  ✓ Zalogowano jako {data['user']['email']}"))
    print(_c(_DIM, f"  Plan:  {plan_tier.upper()}"))
    print(_c(_DIM, f"  Token: {CRED_PATH}"))

    if plan_tier != "epic":
        print()
        print(_c(_LIME, "  ⚠  Pełny agent (chat/REPL) wymaga planu EPIC."))
        print(_c(_DIM, f"     Twój plan: {plan_tier.upper()}"))
        print(_c(_DIM, "     Upgrade: https://www.zaza.net.pl/pricing"))
        print(_c(_DIM, "     Po upgradzie wystarczy: agent-zaza login (token się odnowi)"))
    return creds


def logout() -> None:
    _clear_creds()
    print(_c(_LIME, f"Wylogowano. Plik {CRED_PATH} usunięty."))


def me() -> dict:
    creds = _load_creds()
    if not creds:
        print(_c(_RED, "Nie zalogowano. Uruchom: agent-zaza login"), file=sys.stderr)
        raise SystemExit(1)
    code, data = _http("GET", "/api/agent?action=me", bearer=creds["token"])
    if code != 200 or not data.get("ok"):
        if data.get("error") == "invalid_or_revoked_token":
            print(
                _c(
                    _RED,
                    "Token unieważniony (plan EPIC wygasł?). Zaloguj ponownie.",
                ),
                file=sys.stderr,
            )
        else:
            print(_c(_RED, f"Błąd: {data.get('error', code)}"), file=sys.stderr)
        raise SystemExit(1)
    return data


# ----------------------------------------------------------------------
# Plan gating — REQUIRES EPIC
# ----------------------------------------------------------------------
def _print_epic_required(tier: str) -> None:
    print(BANNER)
    print(
        _c(
            _RED,
            "  ⚠  AGENT ZAZA wymaga aktywnego planu EPIC.",
        )
    )
    print(
        _c(
            _DIM,
            f"  Twój aktualny plan: {tier.upper() if tier else 'BRAK'}",
        )
    )
    print()
    print("  Upgrade na EPIC:")
    print(_c(_LIME, "    https://www.zaza.net.pl/pricing"))
    print()
    print(
        _c(
            _DIM,
            "  Po opłaceniu EPIC odpal: agent-zaza login\n"
            "  (token jest re-walidowany — wystarczy nowa sesja).",
        )
    )


def require_epic() -> dict:
    """Top-level guard. Called from cli.py before agent core initialization.
    Returns the {user, plan} payload on success. Exits otherwise."""
    creds = _load_creds()
    if not creds:
        creds = login()
    code, data = _http("GET", "/api/agent?action=me", bearer=creds["token"])
    if code != 200 or not data.get("ok"):
        # Token revoked / expired — force re-login.
        if data.get("error") == "invalid_or_revoked_token":
            print(
                _c(
                    _DIM,
                    "Token unieważniony — wymagany ponowny login.",
                )
            )
            _clear_creds()
            creds = login()
            code, data = _http(
                "GET", "/api/agent?action=me", bearer=creds["token"]
            )
        if code != 200 or not data.get("ok"):
            print(
                _c(_RED, f"Błąd weryfikacji: {data.get('error', code)}"),
                file=sys.stderr,
            )
            raise SystemExit(1)

    tier = (data.get("plan") or {}).get("tier", "free")
    if tier != "epic":
        _print_epic_required(tier)
        raise SystemExit(2)

    # OK — set env hints that downstream modules can read.
    os.environ.setdefault("ZAZA_AUTH_EMAIL", data["user"]["email"])
    os.environ.setdefault("ZAZA_AUTH_TIER", tier)
    # Refresh provider .env on every validated launch — covers users that
    # logged in before the OpenAI-compat shim existed.
    _write_provider_env(creds["token"])
    return data


# ----------------------------------------------------------------------
# Standalone entrypoint — `python -m zaza_auth <cmd>`
# ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Subcommand-only entry: login / logout / me / guard."""
    args = argv if argv is not None else sys.argv[1:]
    cmd = (args[0] if args else "").lower()
    try:
        if cmd in ("", "guard", "require-epic"):
            require_epic()
            print(_c(_LIME, "✓ EPIC plan aktywny. Możesz uruchomić Agent ZAZA."))
            return 0
        if cmd == "login":
            login()
            return 0
        if cmd == "logout":
            logout()
            return 0
        if cmd in ("me", "whoami"):
            data = me()
            print(_c(_LIME, "Email: ") + data["user"]["email"])
            print(_c(_LIME, "Plan:  ") + (data.get("plan") or {}).get("tier", "?"))
            return 0
        print(f"Nieznana komenda: {cmd}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(_c(_DIM, "\n  przerwano."))
        return 130


def cli_main() -> int:
    """Full pip-installed entry point for `agent-zaza`.

    Dispatches login/logout/me to local handlers; for everything else, gates by
    EPIC plan and then hands off to the agent core (`fire.Fire(main)`).
    Registered in pyproject.toml as `agent-zaza = "zaza_auth:cli_main"`.
    """
    args = sys.argv[1:]
    first = (args[0] if args else "").lower()

    # Subcommand short-circuit (no agent core init required)
    if first in ("login", "logout", "me", "whoami"):
        return main(args)
    if first in ("--help", "-h", "help"):
        print(BANNER)
        print(_c(_LIME, "  agent-zaza login          ") + "— zaloguj się")
        print(_c(_LIME, "  agent-zaza logout         ") + "— usuń lokalny token")
        print(_c(_LIME, "  agent-zaza me             ") + "— pokaż konto + plan")
        print(_c(_LIME, "  agent-zaza                ") + "— uruchom agenta (REPL)")
        print(_c(_LIME, "  agent-zaza -q \"prompt\"    ") + "— jednorazowe pytanie")
        print()
        print(_c(_DIM, "  Wymaga aktywnego planu EPIC. Strona: https://www.zaza.net.pl/pricing"))
        return 0
    if first in ("--version", "-v", "version"):
        print(f"agent-zaza {VERSION}")
        return 0

    # Default: enforce EPIC plan, then run the agent core
    if os.environ.get("ZAZA_SKIP_AUTH") != "1":
        try:
            require_epic()
        except SystemExit as e:
            return int(e.code) if e.code else 0

    # Bootstrap default MCP capabilities (Serena, Chrome DevTools, Sequential
    # Thinking, persistent memory). Idempotent — guarded by a stamp file. The
    # upstream cli.py only triggers this when invoked as `python cli.py`, but
    # our `agent-zaza` entry point goes through fire.Fire(main) which skips
    # __main__, so we replicate the bootstrap here.
    try:
        import zaza_defaults as _zdefaults
        _zdefaults.ensure_zaza_defaults()
    except Exception as _e:
        print(f"[zaza] defaults skipped: {_e}", file=sys.stderr)

    # Lazy-import the agent core (heavy) only after gating passes
    try:
        import fire
    except ImportError:
        print(
            _c(_RED, "Brak `fire` — uruchom: pip install -e ."),
            file=sys.stderr,
        )
        return 1
    try:
        import cli as _agent_core
    except ImportError as e:
        print(_c(_RED, f"Agent core niedostępny: {e}"), file=sys.stderr)
        return 1
    fire.Fire(_agent_core.main)
    return 0


if __name__ == "__main__":
    sys.exit(main())
