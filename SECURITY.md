# Agent ZAZA — Security Policy

## Reporting

Security vulnerabilities should be reported privately via:

- GitHub Security Advisories: <https://github.com/sotius1/agent-zaza/security/advisories/new>
- Email: **jk.butryn@gmail.com** (subject prefix: `[SECURITY] agent-zaza`)

Do **not** open public issues for security vulnerabilities. Please include a reproduction, impact analysis, and CVSS estimate where possible. We aim to acknowledge within 72 hours.

## Trust model

Agent ZAZA is a **single-user, local CLI**. The trust boundary is the user's machine — credentials, generated artifacts, and tool outputs are local-only.

Network boundaries:

- `https://www.zaza.net.pl/api/agent` — auth gate. Email + password over TLS, token returned and stored at `~/.config/agent-zaza/credentials.json` (mode `0600`).
- LLM providers configured in `~/.agent-zaza/config.yaml` — outbound only.
- MCP servers — process-local stdio or user-configured HTTP endpoints.

## Hardening

- **Credentials**: `~/.config/agent-zaza/credentials.json` is created with `0600` (file) and `0700` (directory). Never commit, never log.
- **Browser harness**: launches a **separate Chrome profile** under `~/.agent-zaza/chrome-debug/` — does not share cookies or storage with your everyday browser.
- **MCP servers**: each server runs as a sandboxed subprocess. Review the `command`/`args` of any third-party MCP server before adding it to your config.
- **Plan revalidation**: every session re-validates the EPIC plan against the backend before initializing the agent loop.

## Supply chain

Pinned dependency ranges in `pyproject.toml` reduce drift. Critical CVEs (e.g. requests, PyJWT) are tracked and bumped in patch releases. Run `pip install -e .` after pulling — venv is yours.

## Disclosure timeline

We follow [coordinated disclosure](https://en.wikipedia.org/wiki/Coordinated_disclosure):

- Day 0: report received → ack within 72h
- Day 1–14: investigation + fix
- Day 14–30: patch release + GHSA published
- Day 30+: full public details (CVE if assigned)

Critical vulnerabilities may shorten this timeline.
