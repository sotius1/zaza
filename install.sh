#!/usr/bin/env bash
# Agent ZAZA — one-line installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/sotius1/zaza/master/install.sh | bash
#   # or:
#   bash install.sh
#
# Installs Agent ZAZA into ~/.agent-zaza/ with its own venv, symlinks the
# binary into ~/.local/bin/agent-zaza. Idempotent.

set -euo pipefail

REPO_URL="${AGENT_ZAZA_REPO:-https://github.com/sotius1/zaza.git}"
BRANCH="${AGENT_ZAZA_BRANCH:-master}"
INSTALL_DIR="${AGENT_ZAZA_HOME:-$HOME/.agent-zaza}"
BIN_DIR="${AGENT_ZAZA_BIN:-$HOME/.local/bin}"

LIME='\033[38;2;202;255;51m'
DIM='\033[2m'
RED='\033[31m'
RST='\033[0m'

say() { printf "${LIME}▣${RST} %s\n" "$1"; }
info() { printf "${DIM}  %s${RST}\n" "$1"; }
fail() { printf "${RED}✗ %s${RST}\n" "$1" >&2; exit 1; }

# ---- Preconditions ----
command -v git >/dev/null || fail "git nie znaleziony — zainstaluj git i uruchom ponownie."
command -v python3 >/dev/null || fail "python3 nie znaleziony — wymagany Python ≥ 3.11."

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJ=$(echo "$PY_VER" | cut -d. -f1)
PY_MIN=$(echo "$PY_VER" | cut -d. -f2)
if [[ "$PY_MAJ" -lt 3 || ( "$PY_MAJ" -eq 3 && "$PY_MIN" -lt 11 ) ]]; then
  fail "Python $PY_VER za stary — wymagany ≥ 3.11."
fi

if ! command -v node >/dev/null; then
  info "Node.js nie znaleziony — narzędzia browser-tools nie zostaną zainstalowane (poza tym wszystko działa)."
  HAS_NODE=0
else
  HAS_NODE=1
fi

# uvx — wymagany przez Serena MCP. Auto-instalacja jeśli brak.
if ! command -v uvx >/dev/null 2>&1; then
  if command -v uv >/dev/null 2>&1; then
    info "uv jest, ale uvx nie — sprawdź wersję uv (>= 0.4)."
  else
    say "Instaluję uv (potrzebny dla Serena MCP)"
    if curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1; then
      info "uv zainstalowany"
    else
      info "Auto-install uv nie powiódł się — zainstaluj ręcznie: https://docs.astral.sh/uv/getting-started/installation/"
    fi
    # uv installer placuje binarki w ~/.local/bin lub ~/.cargo/bin
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  fi
fi

# ---- Clone / update ----
if [[ -d "$INSTALL_DIR/.git" ]]; then
  say "Aktualizuję $INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
  git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH" --quiet
else
  say "Klonuję do $INSTALL_DIR"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

# ---- Python venv ----
say "Tworzę venv"
if [[ ! -d "$INSTALL_DIR/.venv" ]]; then
  python3 -m venv "$INSTALL_DIR/.venv"
fi
# shellcheck disable=SC1091
source "$INSTALL_DIR/.venv/bin/activate"
pip install --quiet --upgrade pip
say "Instaluję zależności (to może chwilę potrwać)"
pip install --quiet -e "$INSTALL_DIR"

# ---- Node deps (optional) ----
if [[ "$HAS_NODE" -eq 1 ]]; then
  say "Instaluję narzędzia browser"
  (cd "$INSTALL_DIR" && npm install --silent --no-fund --no-audit) || info "npm install pominięty — wymaga --legacy-peer-deps?"
fi

# ---- MCP server pre-warmup ----
# Provision the four default MCP servers now so the first `agent-zaza` launch
# doesn't show "stdio — failed" while uvx/npx silently provision in the
# background. Each warmup is best-effort: we don't fail the whole install if
# a single MCP server can't be fetched (network blips, cache miss, etc.) —
# the agent still works without that server, just degraded.
say "Pobieram MCP servers (Serena, chrome-devtools, sequential-thinking, memory)"
if command -v uvx >/dev/null 2>&1; then
  info "  • Serena (uvx, ~30s pierwsze pobranie)"
  timeout 180 uvx --from "git+https://github.com/oraios/serena" serena --version >/dev/null 2>&1 \
    && info "    ✓ Serena gotowa" \
    || info "    ⚠ Serena pominięta (sprawdź uvx + sieć)"
fi
if [[ "$HAS_NODE" -eq 1 ]]; then
  info "  • chrome-devtools-mcp (npm cache)"
  timeout 120 npx -y chrome-devtools-mcp@latest --version >/dev/null 2>&1 \
    && info "    ✓ chrome-devtools-mcp gotowe" \
    || info "    ⚠ chrome-devtools-mcp pominięte"
  info "  • sequential-thinking (npm cache)"
  echo "" | timeout 60 npx -y @modelcontextprotocol/server-sequential-thinking >/dev/null 2>&1 \
    || true
  info "    ✓ sequential-thinking gotowe (cache wypełniony)"
  info "  • memory (npm cache)"
  echo "" | timeout 60 npx -y @modelcontextprotocol/server-memory >/dev/null 2>&1 \
    || true
  info "    ✓ memory gotowe (cache wypełniony)"
fi

# ---- Symlink to PATH ----
mkdir -p "$BIN_DIR"
ln -sf "$INSTALL_DIR/.venv/bin/agent-zaza" "$BIN_DIR/agent-zaza"
say "Zainstalowano: $BIN_DIR/agent-zaza"

# ---- PATH hint ----
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    info "Dodaj $BIN_DIR do PATH:"
    info "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
    info "  source ~/.bashrc"
    ;;
esac

cat <<EOF

${LIME}═══════════════════════════════════════════════════${RST}
  ${LIME}AGENT ZAZA${RST} zainstalowany.

  Pierwszy krok:
    ${LIME}agent-zaza login${RST}

  (zapyta o email + hasło — te same co na zaza.net.pl)

  Wymaga aktywnego planu EPIC do uruchomienia agenta:
    ${LIME}https://www.zaza.net.pl/pricing${RST}
${LIME}═══════════════════════════════════════════════════${RST}
EOF
