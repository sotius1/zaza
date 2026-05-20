# Langfuse Observability Plugin

This plugin ships bundled with ZAZA but is **opt-in** — it only loads when
you explicitly enable it.

## Enable

Pick one:

```bash
# Interactive: walks you through credentials + SDK install + enable
zaza tools  # → Langfuse Observability

# Manual
pip install langfuse
zaza plugins enable observability/langfuse
```

## Required credentials

Set these in `~/.agent-zaza/data/.env` (or via `zaza tools`):

```bash
ZAZA_LANGFUSE_PUBLIC_KEY=pk-lf-...
ZAZA_LANGFUSE_SECRET_KEY=sk-lf-...
ZAZA_LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or your self-hosted URL
```

Without the SDK or credentials the hooks no-op silently — the plugin fails
open.

## Verify

```bash
zaza plugins list                 # observability/langfuse should show "enabled"
zaza chat -q "hello"              # then check Langfuse for a "ZAZA turn" trace
```

## Optional tuning

```bash
ZAZA_LANGFUSE_ENV=production       # environment tag
ZAZA_LANGFUSE_RELEASE=v1.0.0       # release tag
ZAZA_LANGFUSE_SAMPLE_RATE=0.5      # sample 50% of traces
ZAZA_LANGFUSE_MAX_CHARS=12000      # max chars per field (default: 12000)
ZAZA_LANGFUSE_DEBUG=true           # verbose plugin logging
```

## Disable

```bash
zaza plugins disable observability/langfuse
```
