#!/usr/bin/env python3
"""Browser Harness tool — direct CDP control over the user's real Chrome.

Wraps the `browser-harness` CLI (https://github.com/browser-use/browser-harness).
The harness exposes a thin Python execution environment with helpers like
`new_tab`, `goto_url`, `wait_for_load`, `page_info`, `capture_screenshot`,
`click_at_xy`, `js`, `cdp`, plus self-healing edits to `helpers.py`.

The agent passes a Python snippet via the ``script`` argument; we invoke
``browser-harness -c <script>`` and return stdout/stderr.

Daemon, websocket, and Chrome attach are managed by the CLI itself. First-time
use requires the user to enable remote debugging on chrome://inspect once.
"""

import logging
import os
import shutil
import subprocess
from typing import Optional

from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

BROWSER_HARNESS_BIN = shutil.which("browser-harness") or os.path.expanduser(
    "~/.local/bin/browser-harness"
)
DEFAULT_TIMEOUT = 120
MAX_OUTPUT_CHARS = 60_000


def _truncate(s: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(s) <= limit:
        return s
    head = s[: limit // 2]
    tail = s[-limit // 2 :]
    return f"{head}\n...[truncated {len(s) - limit} chars]...\n{tail}"


def browser_harness_run(
    script: str,
    timeout: int = DEFAULT_TIMEOUT,
    bu_name: Optional[str] = None,
    cdp_ws: Optional[str] = None,
) -> str:
    """Execute a Python snippet inside browser-harness with helpers preloaded.

    Args:
        script: Python source to run. Helpers (new_tab, goto_url, page_info,
            capture_screenshot, click_at_xy, js, cdp, http_get, ensure_real_tab,
            start_remote_daemon, list_cloud_profiles, etc.) are pre-imported.
        timeout: Hard subprocess timeout in seconds.
        bu_name: Optional BU_NAME — namespaces the daemon socket/pid. Use a
            distinct name per parallel session.
        cdp_ws: Optional BU_CDP_WS — connect to a remote browser (Browser Use
            cloud, etc.) instead of local Chrome.

    Returns:
        Stdout of the script. Stderr is appended on non-zero exit.
    """
    if not script or not script.strip():
        return tool_error("empty script")

    if not os.path.exists(BROWSER_HARNESS_BIN):
        return tool_error(
            "browser-harness CLI not found. Install with: "
            "cd ~/Developer/browser-harness && uv tool install -e ."
        )

    env = os.environ.copy()
    if bu_name:
        env["BU_NAME"] = bu_name
    if cdp_ws:
        env["BU_CDP_WS"] = cdp_ws

    try:
        proc = subprocess.run(
            [BROWSER_HARNESS_BIN, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        partial = (e.stdout or "") + (e.stderr or "")
        return tool_error(f"browser-harness timed out after {timeout}s\n{_truncate(partial)}")
    except Exception as e:
        return tool_error(f"browser-harness exec failed: {e}")

    out = proc.stdout or ""
    err = proc.stderr or ""

    if proc.returncode != 0:
        return tool_error(
            f"browser-harness exit {proc.returncode}\n"
            f"--- stdout ---\n{_truncate(out)}\n"
            f"--- stderr ---\n{_truncate(err)}"
        )

    if err.strip():
        return _truncate(out + ("\n--- stderr ---\n" + err if err.strip() else ""))
    return _truncate(out)


def browser_harness_doctor() -> str:
    """Diagnose install, daemon, and browser state."""
    if not os.path.exists(BROWSER_HARNESS_BIN):
        return tool_error("browser-harness CLI not installed")
    try:
        proc = subprocess.run(
            [BROWSER_HARNESS_BIN, "--doctor"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as e:
        return tool_error(f"doctor failed: {e}")
    return _truncate((proc.stdout or "") + (proc.stderr or ""))


def browser_harness_reload() -> str:
    """Stop the daemon so the next call picks up code changes (e.g. after the
    agent edits helpers.py to add a new function)."""
    if not os.path.exists(BROWSER_HARNESS_BIN):
        return tool_error("browser-harness CLI not installed")
    try:
        proc = subprocess.run(
            [BROWSER_HARNESS_BIN, "--reload"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as e:
        return tool_error(f"reload failed: {e}")
    return _truncate((proc.stdout or "") + (proc.stderr or ""))


def check_browser_harness_requirements() -> tuple[bool, str]:
    if not os.path.exists(BROWSER_HARNESS_BIN):
        return False, (
            "browser-harness CLI missing. Install: "
            "git clone https://github.com/browser-use/browser-harness ~/Developer/browser-harness "
            "&& cd ~/Developer/browser-harness && uv tool install -e ."
        )
    return True, ""


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

BROWSER_HARNESS_RUN_SCHEMA = {
    "name": "browser_harness_run",
    "description": (
        "Execute Python directly inside the user's running Chrome via the "
        "browser-harness CDP harness. Helpers are pre-imported: new_tab(url), "
        "goto_url(url), wait_for_load(), page_info(), capture_screenshot(), "
        "click_at_xy(x, y), js(expr), cdp(method, params), http_get(url), "
        "ensure_real_tab(), start_remote_daemon(name), list_cloud_profiles(), "
        "list_local_profiles(), sync_local_profile(). For first navigation use "
        "new_tab() not goto_url() (goto clobbers the user's active tab). "
        "After actions, verify with capture_screenshot() or page_info(). "
        "If a helper is missing, edit ~/Developer/browser-harness/helpers.py "
        "to add it, then call browser_harness_reload."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "script": {
                "type": "string",
                "description": (
                    "Python source. Use print() to return data. "
                    "Example: 'new_tab(\"https://example.com\"); wait_for_load(); print(page_info())'"
                ),
            },
            "timeout": {
                "type": "integer",
                "description": "Subprocess timeout in seconds (default 120).",
                "default": DEFAULT_TIMEOUT,
            },
            "bu_name": {
                "type": "string",
                "description": (
                    "Optional BU_NAME — namespaces the daemon. Use distinct "
                    "names for parallel sessions."
                ),
            },
            "cdp_ws": {
                "type": "string",
                "description": (
                    "Optional BU_CDP_WS — wss URL for a remote Browser Use "
                    "cloud browser. Skip for local Chrome."
                ),
            },
        },
        "required": ["script"],
    },
}

BROWSER_HARNESS_DOCTOR_SCHEMA = {
    "name": "browser_harness_doctor",
    "description": (
        "Diagnose browser-harness install: version, daemon state, Chrome "
        "attach state, pending updates."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

BROWSER_HARNESS_RELOAD_SCHEMA = {
    "name": "browser_harness_reload",
    "description": (
        "Stop the browser-harness daemon so the next call picks up code "
        "changes. Call after editing helpers.py to add a missing helper."
    ),
    "input_schema": {"type": "object", "properties": {}},
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

registry.register(
    name="browser_harness_run",
    toolset="browser_harness",
    schema=BROWSER_HARNESS_RUN_SCHEMA,
    handler=lambda args, **kw: browser_harness_run(
        script=args.get("script", ""),
        timeout=int(args.get("timeout", DEFAULT_TIMEOUT)),
        bu_name=args.get("bu_name"),
        cdp_ws=args.get("cdp_ws"),
    ),
    check_fn=check_browser_harness_requirements,
    emoji="♞",
    description="Direct CDP browser control via browser-harness",
    max_result_size_chars=MAX_OUTPUT_CHARS,
)

registry.register(
    name="browser_harness_doctor",
    toolset="browser_harness",
    schema=BROWSER_HARNESS_DOCTOR_SCHEMA,
    handler=lambda args, **kw: browser_harness_doctor(),
    check_fn=check_browser_harness_requirements,
    emoji="🩺",
    description="Diagnose browser-harness install + daemon",
)

registry.register(
    name="browser_harness_reload",
    toolset="browser_harness",
    schema=BROWSER_HARNESS_RELOAD_SCHEMA,
    handler=lambda args, **kw: browser_harness_reload(),
    check_fn=check_browser_harness_requirements,
    emoji="🔄",
    description="Reload browser-harness daemon after editing helpers.py",
)
