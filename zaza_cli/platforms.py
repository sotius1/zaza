"""
Shared platform registry for Agent ZAZA.

Single source of truth for platform metadata consumed by both
skills_config (label display) and tools_config (default toolset
resolution).  Import ``PLATFORMS`` from here instead of maintaining
duplicate dicts in each module.
"""

from collections import OrderedDict
from typing import NamedTuple


class PlatformInfo(NamedTuple):
    """Metadata for a single platform entry."""
    label: str
    default_toolset: str


# Ordered so that TUI menus are deterministic.
PLATFORMS: OrderedDict[str, PlatformInfo] = OrderedDict([
    ("cli",            PlatformInfo(label="🖥️  CLI",            default_toolset="agent-zaza-cli-toolset")),
    ("telegram",       PlatformInfo(label="📱 Telegram",        default_toolset="zaza-telegram")),
    ("discord",        PlatformInfo(label="💬 Discord",         default_toolset="zaza-discord")),
    ("slack",          PlatformInfo(label="💼 Slack",           default_toolset="zaza-slack")),
    ("whatsapp",       PlatformInfo(label="📱 WhatsApp",        default_toolset="zaza-whatsapp")),
    ("signal",         PlatformInfo(label="📡 Signal",          default_toolset="zaza-signal")),
    ("bluebubbles",    PlatformInfo(label="💙 BlueBubbles",     default_toolset="zaza-bluebubbles")),
    ("email",          PlatformInfo(label="📧 Email",           default_toolset="zaza-email")),
    ("homeassistant",  PlatformInfo(label="🏠 Home Assistant",  default_toolset="zaza-homeassistant")),
    ("mattermost",     PlatformInfo(label="💬 Mattermost",      default_toolset="zaza-mattermost")),
    ("matrix",         PlatformInfo(label="💬 Matrix",          default_toolset="zaza-matrix")),
    ("dingtalk",       PlatformInfo(label="💬 DingTalk",        default_toolset="zaza-dingtalk")),
    ("feishu",         PlatformInfo(label="🪽 Feishu",          default_toolset="zaza-feishu")),
    ("wecom",          PlatformInfo(label="💬 WeCom",           default_toolset="zaza-wecom")),
    ("wecom_callback", PlatformInfo(label="💬 WeCom Callback",  default_toolset="zaza-wecom-callback")),
    ("weixin",         PlatformInfo(label="💬 Weixin",          default_toolset="zaza-weixin")),
    ("qqbot",          PlatformInfo(label="💬 QQBot",           default_toolset="zaza-qqbot")),
    ("yuanbao",        PlatformInfo(label="🤖 Yuanbao",         default_toolset="zaza-yuanbao")),
    ("webhook",        PlatformInfo(label="🔗 Webhook",         default_toolset="zaza-webhook")),
    ("api_server",     PlatformInfo(label="🌐 API Server",      default_toolset="agent-zaza-api-server")),
    ("cron",           PlatformInfo(label="⏰ Cron",            default_toolset="agent-zaza-cron")),
])


def platform_label(key: str, default: str = "") -> str:
    """Return the display label for a platform key, or *default*."""
    info = PLATFORMS.get(key)
    return info.label if info is not None else default
