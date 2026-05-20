# Agent ZAZA

> Pełnowymiarowy agent AI w terminalu — flagowy CLI ze studia [zaza.net.pl](https://www.zaza.net.pl).

## Co to jest

**Agent ZAZA** to lokalny agent AI z pełnym pakietem tooli typowych dla state-of-the-art agentów programistycznych. Po jednej komendzie `agent-zaza` masz w terminalu:

- **Serena** — LSP-driven code intelligence. Symboliczna nawigacja po kodzie (klasy, metody, referencje), surgical edits zamiast nadpisywania całych plików.
- **Browser harness** — sandboxowany Chrome przez DevTools Protocol. Live automatyzacja stron www, console & network inspection, screenshoty, performance traces.
- **Persistent memory** — graf wiedzy który przetrwa między sesjami.
- **Sequential thinking** — strukturalne wieloetapowe rozumowanie dla nietrywialnych problemów.
- **File / shell / web tools** — read, edit, run, search, fetch.
- **Skills hub** — dziesiątki gotowych skill packs (web-development, devops, autonomous-ai-agents, security, productivity, …).
- **Cron / batch / gateway** — scheduling, batch runs, persistent server mode.
- **MCP-native** — dowolny MCP server podpinasz jedną komendą.

Agent ZAZA jest **gated** subskrypcją EPIC z [zaza.net.pl/pricing](https://www.zaza.net.pl/pricing). Login przez email + hasło — to samo konto którego używasz na stronie.

## Instalacja

Jedna komenda — automatycznie tworzy venv w `~/.agent-zaza/`, instaluje zależności i podpina `agent-zaza` do `~/.local/bin`:

```bash
curl -fsSL https://raw.githubusercontent.com/sotius1/zaza/master/install.sh | bash
```

Wymagania:

- Python ≥ 3.11
- Node.js ≥ 20 (dla browser tools i wielu MCP serwerów)
- `uv` / `uvx` — automatycznie instalowane jeśli brak (Serena MCP wymaga `uvx`)
- Chrome / Chromium — dla browser harness

## Pierwszy start

```bash
agent-zaza login         # email + hasło z zaza.net.pl (plan EPIC)
agent-zaza               # uruchamia agenta z pełnym tool-callingiem
```

Przy pierwszym `agent-zaza` system zrobi bootstrap defaults — Serena, Chrome DevTools, Sequential Thinking, Memory MCP zostają zarejestrowane w `~/.agent-zaza/config.yaml`. Możesz zmienić cokolwiek po fakcie:

```bash
agent-zaza mcp list                 # co jest podłączone
agent-zaza mcp remove <name>        # usuń konkretny server
agent-zaza mcp add <name> ...       # dodaj swój
```

## Komendy

| Komenda                             | Działanie                                                                    |
| ----------------------------------- | ---------------------------------------------------------------------------- |
| `agent-zaza`                        | interaktywny REPL agenta                                                     |
| `agent-zaza -q "pytanie"`           | tryb one-shot                                                                |
| `agent-zaza login`                  | login email + hasło → zapis tokenu w `~/.config/agent-zaza/credentials.json` |
| `agent-zaza logout`                 | wyloguj                                                                      |
| `agent-zaza me`                     | aktualny user + plan                                                         |
| `agent-zaza status`                 | health check (auth, providers, MCP, browser, gateway)                        |
| `agent-zaza mcp <add/list/remove>`  | zarządzaj MCP serwerami                                                      |
| `agent-zaza skills <list/install>`  | skill packs                                                                  |
| `agent-zaza cron <add/list/remove>` | scheduled runs                                                               |
| `agent-zaza gateway <start/stop>`   | tryb daemon (zdalne wywołania)                                               |
| `agent-zaza batch <run>`            | batch processing                                                             |
| `agent-zaza web`                    | uruchom Web UI                                                               |

## Konfiguracja

| Zmienna           | Domyślnie                 | Opis                                  |
| ----------------- | ------------------------- | ------------------------------------- |
| `ZAZA_API_BASE`   | `https://www.zaza.net.pl` | URL backendu (preview/local override) |
| `ZAZA_SKIP_AUTH`  | unset                     | `=1` pomija gating (dev only)         |
| `ZAZA_QUIET`      | unset                     | `=1` ucisza logi bootstrap            |
| `XDG_CONFIG_HOME` | `~/.config`               | dir bazowy dla credentials            |

Token (`~/.config/agent-zaza/credentials.json`) ma uprawnienia `0600`, dir `0700`. Konfiguracja agenta i skills lądują w `~/.agent-zaza/`.

## Architektura w skrócie

```
agent-zaza CLI
  ├── auth gate (email+hasło → /api/agent → wymaga planu EPIC)
  ├── defaults bootstrap (Serena · Chrome DevTools · Sequential Thinking · Memory)
  ├── core
  │   ├── tools: file · shell · web · browser · MCP
  │   ├── providers: OpenAI · Anthropic · multiple (auto-routing)
  │   ├── skills: curated + custom packs
  │   └── memory: persistent knowledge graph
  └── modes: interactive · one-shot · batch · cron · gateway · web UI
```

## Bezpieczeństwo

- Credentials w `~/.config/agent-zaza/credentials.json` (mode `0600`).
- Browser harness uruchamia osobną instancję Chrome z dedykowanym profile dir — bez dostępu do twoich normalnych ciasteczek.
- MCP serwery sandboxowane do osobnych procesów stdio/HTTP.
- Plan EPIC re-walidowany przy każdej sesji (token refresh).

Zobacz `SECURITY.md` po szczegóły.

## Licencja

MIT — patrz `LICENSE`.

---

**Studio ZAZA · zaza.net.pl** — _kontakt:_ [jk.butryn@gmail.com](mailto:jk.butryn@gmail.com) · [t.me/sotius](https://t.me/sotius)
