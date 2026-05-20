"""Welcome banner, ASCII art, skills summary, and update check for the CLI.

Pure display functions with no ZazaCLI state dependency.
"""

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from zaza_constants import get_zaza_home
from typing import Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from prompt_toolkit import print_formatted_text as _pt_print
from prompt_toolkit.formatted_text import ANSI as _PT_ANSI

logger = logging.getLogger(__name__)


# =========================================================================
# ANSI building blocks for conversation display
# =========================================================================

_GOLD = "\033[1;38;2;255;215;0m"  # True-color #FFD700 bold
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RST = "\033[0m"


def cprint(text: str):
    """Print ANSI-colored text through prompt_toolkit's renderer."""
    _pt_print(_PT_ANSI(text))


# =========================================================================
# Skin-aware color helpers
# =========================================================================

def _skin_color(key: str, fallback: str) -> str:
    """Get a color from the active skin, or return fallback."""
    try:
        from zaza_cli.skin_engine import get_active_skin
        return get_active_skin().get_color(key, fallback)
    except Exception:
        return fallback


def _skin_branding(key: str, fallback: str) -> str:
    """Get a branding string from the active skin, or return fallback."""
    try:
        from zaza_cli.skin_engine import get_active_skin
        return get_active_skin().get_branding(key, fallback)
    except Exception:
        return fallback


# =========================================================================
# ASCII Art & Branding
# =========================================================================

from zaza_cli import __version__ as VERSION, __release_date__ as RELEASE_DATE

# ZAZA-native logo — minimalist block letters (cyber-green/violet gradient)
# Renders only when terminal width >= 70 cols, otherwise compact banner is used.
ZAZA_AGENT_LOGO = """[bold #00ff88]  ▀▀█ ▄▀█ ▀▀█ ▄▀█  [/]
[bold #00ff88]  ▄▀  █▀█ ▄▀  █▀█  [/]
[bold #a855f7]  ▀▀▀ ▀ ▀ ▀▀▀ ▀ ▀  [/]"""

# Hero art on the left side of the welcome panel.
# Empty by default — minimal, developer-grade aesthetic. Skin can override
# via SkinConfig.banner_hero if a skin wants its own hero art.
ZAZA_CADUCEUS = ""



# =========================================================================
# Skills scanning
# =========================================================================

def get_available_skills() -> Dict[str, List[str]]:
    """Return skills grouped by category, filtered by platform and disabled state.

    Delegates to ``_find_all_skills()`` from ``tools/skills_tool`` which already
    handles platform gating (``platforms:`` frontmatter) and respects the
    user's ``skills.disabled`` config list.
    """
    try:
        from tools.skills_tool import _find_all_skills
        all_skills = _find_all_skills()  # already filtered
    except Exception:
        return {}

    skills_by_category: Dict[str, List[str]] = {}
    for skill in all_skills:
        category = skill.get("category") or "general"
        skills_by_category.setdefault(category, []).append(skill["name"])
    return skills_by_category


# =========================================================================
# Update check
# =========================================================================

# Cache update check results for 6 hours to avoid repeated git fetches
_UPDATE_CHECK_CACHE_SECONDS = 6 * 3600

# Sentinel returned when we know an update exists but can't count commits
# (e.g. nix-built install — no local git history to count against).
UPDATE_AVAILABLE_NO_COUNT = -1

_UPSTREAM_REPO_URL = "https://github.com/sotius1/agent-zaza.git"


def _check_via_rev(local_rev: str) -> Optional[int]:
    """Compare an embedded git revision to upstream main via ls-remote.

    Returns 0 if up-to-date, ``UPDATE_AVAILABLE_NO_COUNT`` if behind,
    or ``None`` on failure.
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", _UPSTREAM_REPO_URL, "refs/heads/main"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    upstream_rev = result.stdout.split()[0]
    if not upstream_rev:
        return None
    return 0 if upstream_rev == local_rev else UPDATE_AVAILABLE_NO_COUNT


def _check_via_local_git(repo_dir: Path) -> Optional[int]:
    """Count commits behind origin/main in a local checkout."""
    try:
        subprocess.run(
            ["git", "fetch", "origin", "--quiet"],
            capture_output=True, timeout=10,
            cwd=str(repo_dir),
        )
    except Exception:
        pass  # Offline or timeout — use stale refs, that's fine

    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/main"],
            capture_output=True, text=True, timeout=5,
            cwd=str(repo_dir),
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return None


def check_for_updates() -> Optional[int]:
    """Check whether an Agent ZAZA update is available.

    Two paths: if ``ZAZA_REVISION`` is set (nix builds embed it), compare
    it to upstream main via ``git ls-remote``. Otherwise look for a local
    git checkout and count commits behind ``origin/main``.

    Returns the number of commits behind, ``UPDATE_AVAILABLE_NO_COUNT`` (-1)
    if behind but the count is unknown, ``0`` if up-to-date, or ``None`` if
    the check failed or doesn't apply. Cached for 6 hours.
    """
    zaza_home = get_zaza_home()
    cache_file = zaza_home / ".update_check"
    embedded_rev = os.environ.get("ZAZA_REVISION") or None

    # Read cache — invalidate if the embedded rev has changed since last check
    now = time.time()
    try:
        if cache_file.exists():
            cached = json.loads(cache_file.read_text())
            if (
                now - cached.get("ts", 0) < _UPDATE_CHECK_CACHE_SECONDS
                and cached.get("rev") == embedded_rev
            ):
                return cached.get("behind")
    except Exception:
        pass

    if embedded_rev:
        behind = _check_via_rev(embedded_rev)
    else:
        repo_dir = zaza_home / "agent-zaza"
        if not (repo_dir / ".git").exists():
            repo_dir = Path(__file__).parent.parent.resolve()
        if not (repo_dir / ".git").exists():
            return None
        behind = _check_via_local_git(repo_dir)

    try:
        cache_file.write_text(json.dumps({"ts": now, "behind": behind, "rev": embedded_rev}))
    except Exception:
        pass

    return behind


def _resolve_repo_dir() -> Optional[Path]:
    """Return the active Agent ZAZA git checkout, or None if this isn't a git install."""
    zaza_home = get_zaza_home()
    repo_dir = zaza_home / "agent-zaza"
    if not (repo_dir / ".git").exists():
        repo_dir = Path(__file__).parent.parent.resolve()
    return repo_dir if (repo_dir / ".git").exists() else None


def _git_short_hash(repo_dir: Path, rev: str) -> Optional[str]:
    """Resolve a git revision to an 8-character short hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=8", rev],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(repo_dir),
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    value = (result.stdout or "").strip()
    return value or None


def get_git_banner_state(repo_dir: Optional[Path] = None) -> Optional[dict]:
    """Return upstream/local git hashes for the startup banner."""
    repo_dir = repo_dir or _resolve_repo_dir()
    if repo_dir is None:
        return None

    upstream = _git_short_hash(repo_dir, "origin/main")
    local = _git_short_hash(repo_dir, "HEAD")
    if not upstream or not local:
        return None

    ahead = 0
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "origin/main..HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(repo_dir),
        )
        if result.returncode == 0:
            ahead = int((result.stdout or "0").strip() or "0")
    except Exception:
        ahead = 0

    return {"upstream": upstream, "local": local, "ahead": max(ahead, 0)}


_RELEASE_URL_BASE = "https://github.com/sotius1/agent-zaza/releases/tag"
_latest_release_cache: Optional[tuple] = None  # (tag, url) once resolved


def get_latest_release_tag(repo_dir: Optional[Path] = None) -> Optional[tuple]:
    """Return ``(tag, release_url)`` for the latest git tag, or None.

    Local-only — runs ``git describe --tags --abbrev=0`` against the local
    Agent ZAZA checkout. Cached per-process.
    """
    global _latest_release_cache
    if _latest_release_cache is not None:
        return _latest_release_cache or None

    repo_dir = repo_dir or _resolve_repo_dir()
    if repo_dir is None:
        _latest_release_cache = ()  # falsy sentinel — skip future lookups
        return None

    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=str(repo_dir),
        )
    except Exception:
        _latest_release_cache = ()
        return None

    if result.returncode != 0:
        _latest_release_cache = ()
        return None

    tag = (result.stdout or "").strip()
    if not tag:
        _latest_release_cache = ()
        return None

    url = f"{_RELEASE_URL_BASE}/{tag}"
    _latest_release_cache = (tag, url)
    return _latest_release_cache


def format_banner_version_label() -> str:
    """Return the version label shown in the startup banner title."""
    base = f"Agent ZAZA v{VERSION} ({RELEASE_DATE})"
    state = get_git_banner_state()
    if not state:
        return base

    upstream = state["upstream"]
    local = state["local"]
    ahead = int(state.get("ahead") or 0)

    if ahead <= 0 or upstream == local:
        return f"{base} · upstream {upstream}"

    carried_word = "commit" if ahead == 1 else "commits"
    return f"{base} · upstream {upstream} · local {local} (+{ahead} carried {carried_word})"


# =========================================================================
# Non-blocking update check
# =========================================================================

_update_result: Optional[int] = None
_update_check_done = threading.Event()


def prefetch_update_check():
    """Kick off update check in a background daemon thread."""
    def _run():
        global _update_result
        _update_result = check_for_updates()
        _update_check_done.set()
    t = threading.Thread(target=_run, daemon=True)
    t.start()


def get_update_result(timeout: float = 0.5) -> Optional[int]:
    """Get result of prefetched check. Returns None if not ready."""
    _update_check_done.wait(timeout=timeout)
    return _update_result


# =========================================================================
# Welcome banner
# =========================================================================

def _format_context_length(tokens: int) -> str:
    """Format a token count for display (e.g. 128000 → '128K', 1048576 → '1M')."""
    if tokens >= 1_000_000:
        val = tokens / 1_000_000
        rounded = round(val)
        if abs(val - rounded) < 0.05:
            return f"{rounded}M"
        return f"{val:.1f}M"
    elif tokens >= 1_000:
        val = tokens / 1_000
        rounded = round(val)
        if abs(val - rounded) < 0.05:
            return f"{rounded}K"
        return f"{val:.1f}K"
    return str(tokens)


def _display_toolset_name(toolset_name: str) -> str:
    """Normalize internal/legacy toolset identifiers for banner display."""
    if not toolset_name:
        return "unknown"
    return (
        toolset_name[:-6]
        if toolset_name.endswith("_tools")
        else toolset_name
    )


def _mcp_status_line(srv: dict, ok_color: str, fail_color: str, dim_color: str) -> str:
    """Render a single MCP server line in the welcome banner.

    Uses ManagedServer's ``connected`` flag as the source of truth.  Avoids
    the historical pitfall where a server emitted its initial ListTools probe
    successfully but the panel still rendered ``failed`` — the panel now
    matches the lifecycle state directly.
    """
    name = srv.get("name", "?")
    transport = srv.get("transport", "stdio")
    if srv.get("connected"):
        tool_count = srv.get("tools", 0)
        return (
            f"  [{ok_color}]●[/] [bold]{name}[/]  "
            f"[dim {dim_color}]{transport} · {tool_count} tool(s)[/]"
        )
    return (
        f"  [{fail_color}]○[/] [bold]{name}[/]  "
        f"[dim {dim_color}]{transport} · failed[/]"
    )


def _build_meta_lines(model: str, cwd: str, session_id: Optional[str],
                      context_length: Optional[int],
                      accent: str, dim: str, text: str) -> List[str]:
    """Compose the meta block: model · cwd · session."""
    model_short = model.split("/")[-1] if "/" in model else model
    if model_short.endswith(".gguf"):
        model_short = model_short[:-5]
    if len(model_short) > 32:
        model_short = model_short[:29] + "..."

    ctx_str = ""
    if context_length:
        ctx_str = f"  [dim {dim}]·[/]  [{accent}]{_format_context_length(context_length)}[/] [dim {dim}]context[/]"

    lines = [
        f"  [dim {dim}]model[/]    [{text}]{model_short}[/]{ctx_str}",
        f"  [dim {dim}]cwd[/]      [{text}]{cwd}[/]",
    ]
    if session_id:
        lines.append(f"  [dim {dim}]session[/]  [{text}]{session_id}[/]")
    return lines


def build_welcome_banner(console: Console, model: str, cwd: str,
                         tools: Optional[List[dict]] = None,
                         enabled_toolsets: Optional[List[str]] = None,
                         session_id: Optional[str] = None,
                         get_toolset_for_tool=None,
                         context_length: Optional[int] = None):
    """Print the welcome banner — minimalist, developer-grade aesthetic.

    Layout (full width):

        ▀▀█ ▄▀█ ▀▀█ ▄▀█        ZAZA · v0.5.0 (2026.5.4)
        ▄▀  █▀█ ▄▀  █▀█        upstream 3733fb93
        ▀▀▀ ▀ ▀ ▀▀▀ ▀ ▀

      model    zaza-1.1   ·   128K context
      cwd      /home/zaza
      session  20260504_143000_abc123

      tools    28        file · terminal · browser · code · ...
      mcp      4/4       ● serena   ● chrome   ● thinking   ● memory
      skills   0         —

      /help · komendy                                profile: default

    Source-of-truth for MCP status is ``ManagedServer.connected`` (read via
    ``get_mcp_status()``).  No more rendering ``failed`` for servers whose
    handshake actually succeeded.
    """
    from model_tools import check_tool_availability, TOOLSET_REQUIREMENTS
    if get_toolset_for_tool is None:
        from model_tools import get_toolset_for_tool

    tool_list = tools or []
    enabled_toolsets = enabled_toolsets or []

    _, unavailable_toolsets = check_tool_availability(quiet=True)
    disabled_tools: set = set()
    lazy_tools: set = set()
    for item in unavailable_toolsets:
        toolset_name = item.get("name", "")
        ts_req = TOOLSET_REQUIREMENTS.get(toolset_name, {})
        tools_in_ts = item.get("tools", [])
        if ts_req.get("check_fn"):
            lazy_tools.update(tools_in_ts)
        else:
            disabled_tools.update(tools_in_ts)

    # Skin colors
    accent = _skin_color("banner_accent", "#a855f7")
    dim = _skin_color("banner_dim", "#64748b")
    text = _skin_color("banner_text", "#e2e8f0")
    title_color = _skin_color("banner_title", "#00ff88")
    border_color = _skin_color("banner_border", "#1f2937")
    ok_color = _skin_color("ui_ok", "#22c55e")
    err_color = _skin_color("ui_error", "#ef4444")

    # Header — logo + title (logo from skin if provided)
    try:
        from zaza_cli.skin_engine import get_active_skin
        _bskin = get_active_skin()
        _logo = _bskin.banner_logo if _bskin and getattr(_bskin, 'banner_logo', '') else ZAZA_AGENT_LOGO
    except Exception:
        _bskin = None
        _logo = ZAZA_AGENT_LOGO

    version_label = format_banner_version_label()
    release_info = get_latest_release_tag()
    if release_info:
        _tag, _url = release_info
        title_markup = f"[bold {title_color}][link={_url}]{version_label}[/link][/]"
    else:
        title_markup = f"[bold {title_color}]{version_label}[/]"

    # Header: logo on the left, title block on the right
    header = Table.grid(padding=(0, 4))
    header.add_column(justify="left")
    header.add_column(justify="left")
    header.add_row(_logo, f"\n  {title_markup}\n")

    # Meta block
    meta_lines = _build_meta_lines(model, cwd, session_id, context_length,
                                   title_color, dim, text)

    # Tools block
    toolsets_dict: Dict[str, list] = {}
    for tool in tool_list:
        tool_name = tool["function"]["name"]
        toolset = _display_toolset_name(get_toolset_for_tool(tool_name) or "other")
        toolsets_dict.setdefault(toolset, []).append(tool_name)
    for item in unavailable_toolsets:
        toolset_id = item.get("id", item.get("name", "unknown"))
        display_name = _display_toolset_name(toolset_id)
        toolsets_dict.setdefault(display_name, [])
        for tn in item.get("tools", []):
            if tn not in toolsets_dict[display_name]:
                toolsets_dict[display_name].append(tn)

    sorted_toolsets = sorted(toolsets_dict.keys())
    visible_toolsets = sorted_toolsets[:6]
    overflow = len(sorted_toolsets) - len(visible_toolsets)
    toolset_summary_parts = [f"[{text}]{ts}[/]" for ts in visible_toolsets]
    if overflow > 0:
        toolset_summary_parts.append(f"[dim {dim}]+{overflow} more[/]")
    toolset_summary = f"  [dim {dim}]·[/]  ".join(toolset_summary_parts) or f"[dim {dim}]—[/]"

    # MCP block — read live status (lifecycle-correct)
    try:
        from tools.mcp_tool import get_mcp_status
        mcp_status = get_mcp_status()
    except Exception:
        mcp_status = []
    mcp_connected = sum(1 for s in mcp_status if s.get("connected"))
    mcp_total = len(mcp_status)

    # Skills block
    skills_by_category = get_available_skills()
    total_skills = sum(len(s) for s in skills_by_category.values())

    # Compose body
    body_lines: List[str] = []
    body_lines.extend(meta_lines)
    body_lines.append("")
    body_lines.append(
        f"  [dim {dim}]tools[/]    [{title_color}]{len(tool_list):<3}[/]   {toolset_summary}"
    )
    if mcp_status:
        if mcp_connected == mcp_total:
            mcp_count = f"[{ok_color}]{mcp_connected}/{mcp_total}[/]"
        elif mcp_connected == 0:
            mcp_count = f"[{err_color}]{mcp_connected}/{mcp_total}[/]"
        else:
            mcp_count = f"[{accent}]{mcp_connected}/{mcp_total}[/]"
        markers = "  ".join(
            f"[{ok_color if s.get('connected') else err_color}]"
            f"{'●' if s.get('connected') else '○'}[/] [{text}]{s.get('name', '?')}[/]"
            for s in mcp_status
        )
        body_lines.append(f"  [dim {dim}]mcp[/]      {mcp_count}   {markers}")
    if skills_by_category:
        skill_summary_parts = []
        for cat in sorted(skills_by_category.keys())[:5]:
            skill_summary_parts.append(f"[{text}]{cat}[/]")
        if len(skills_by_category) > 5:
            skill_summary_parts.append(f"[dim {dim}]+{len(skills_by_category) - 5}[/]")
        skill_summary = f"  [dim {dim}]·[/]  ".join(skill_summary_parts)
        body_lines.append(
            f"  [dim {dim}]skills[/]   [{title_color}]{total_skills:<3}[/]   {skill_summary}"
        )
    else:
        body_lines.append(
            f"  [dim {dim}]skills[/]   [dim {dim}]0     —[/]"
        )

    # Footer line — profile + commands hint
    body_lines.append("")
    footer_left = f"  [dim {dim}]/help · komendy[/]"
    profile_label = ""
    try:
        from zaza_cli.profiles import get_active_profile_name
        _profile_name = get_active_profile_name()
        if _profile_name and _profile_name != "default":
            profile_label = f"[dim {dim}]profile:[/] [{accent}]{_profile_name}[/]"
    except Exception:
        pass
    if profile_label:
        body_lines.append(f"{footer_left}        {profile_label}")
    else:
        body_lines.append(footer_left)

    # Update check
    try:
        behind = get_update_result(timeout=0.5)
        if behind is not None and behind != 0:
            from zaza_cli.config import get_managed_update_command, recommended_update_command
            if behind > 0:
                commits_word = "commit" if behind == 1 else "commits"
                body_lines.append(
                    f"  [bold {accent}]⟳ {behind} {commits_word} behind[/] "
                    f"[dim {dim}]— run [bold]{recommended_update_command()}[/bold] to update[/]"
                )
            else:
                managed_cmd = get_managed_update_command()
                line = f"  [bold {accent}]⟳ update available[/]"
                if managed_cmd:
                    line += f" [dim {dim}]— run [bold]{managed_cmd}[/bold][/]"
                body_lines.append(line)
    except Exception:
        pass

    body_content = "\n".join(body_lines)

    outer_panel = Panel(
        body_content,
        title=title_markup,
        title_align="left",
        border_style=border_color,
        padding=(1, 1),
    )

    console.print()
    term_width = shutil.get_terminal_size().columns
    if term_width >= 70:
        console.print(header)
    console.print(outer_panel)
