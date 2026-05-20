# nix/agent-zaza.nix — Overridable Agent ZAZA package
#
# callPackage auto-wires nixpkgs args; flake inputs are passed explicitly.
# Users override via: pkgs.agent-zaza.override { extraPythonPackages = [...]; }
{
  lib,
  stdenv,
  makeWrapper,
  callPackage,
  python312,
  nodejs_22,
  ripgrep,
  git,
  openssh,
  ffmpeg,
  tirith,
  # Flake inputs — passed explicitly by packages.nix and overlays.nix
  uv2nix,
  pyproject-nix,
  pyproject-build-systems,
  npm-lockfile-fix,
  # Locked git revision of the flake source — embedded so banner.py can
  # check for updates without needing a local .git directory. Null for
  # impure / dirty builds where flakes can't determine a rev.
  rev ? null,
  # Overridable parameters
  extraPythonPackages ? [ ],
}:
let
  zazaVenv = callPackage ./python.nix {
    inherit uv2nix pyproject-nix pyproject-build-systems;
  };

  zazaNpmLib = callPackage ./lib.nix {
    inherit npm-lockfile-fix;
  };

  zazaTui = callPackage ./tui.nix {
    inherit zazaNpmLib;
  };

  zazaWeb = callPackage ./web.nix {
    inherit zazaNpmLib;
  };

  bundledSkills = lib.cleanSourceWith {
    src = ../skills;
    filter = path: _type: !(lib.hasInfix "/index-cache/" path);
  };

  runtimeDeps = [
    nodejs_22
    ripgrep
    git
    openssh
    ffmpeg
    tirith
  ];

  runtimePath = lib.makeBinPath runtimeDeps;

  sitePackagesPath = python312.sitePackages;

  # Walk propagatedBuildInputs to include transitive Python deps in PYTHONPATH.
  # Without this, a plugin listing e.g. requests as a dep would fail at runtime
  # if requests isn't already in the sealed uv2nix venv.
  allExtraPythonPackages = python312.pkgs.requiredPythonModules extraPythonPackages;

  pythonPath = lib.makeSearchPath sitePackagesPath allExtraPythonPackages;

  pyprojectHash = builtins.hashString "sha256" (builtins.readFile ../pyproject.toml);
  uvLockHash =
    if builtins.pathExists ../uv.lock then
      builtins.hashString "sha256" (builtins.readFile ../uv.lock)
    else
      "none";
in
stdenv.mkDerivation {
  pname = "agent-zaza";
  version = (builtins.fromTOML (builtins.readFile ../pyproject.toml)).project.version;

  dontUnpack = true;
  dontBuild = true;
  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p $out/share/agent-zaza $out/bin
    cp -r ${bundledSkills} $out/share/agent-zaza/skills
    cp -r ${zazaWeb} $out/share/agent-zaza/web_dist

    mkdir -p $out/ui-tui
    cp -r ${zazaTui}/lib/zaza-tui/* $out/ui-tui/

    ${lib.concatMapStringsSep "\n"
      (name: ''
        makeWrapper ${zazaVenv}/bin/${name} $out/bin/${name} \
          --suffix PATH : "${runtimePath}" \
          --set ZAZA_BUNDLED_SKILLS $out/share/agent-zaza/skills \
          --set ZAZA_WEB_DIST $out/share/agent-zaza/web_dist \
          --set ZAZA_TUI_DIR $out/ui-tui \
          --set ZAZA_PYTHON ${zazaVenv}/bin/python3 \
          --set ZAZA_NODE ${nodejs_22}/bin/node \
          ${lib.optionalString (rev != null) ''--set ZAZA_REVISION ${rev} \''}
          ${lib.optionalString (extraPythonPackages != [ ]) ''--suffix PYTHONPATH : "${pythonPath}"''}
      '')
      [
        "zaza"
        "agent-zaza"
        "zaza-acp"
      ]
    }

    ${lib.optionalString (extraPythonPackages != [ ]) ''
      echo "=== Checking for plugin/core package collisions ==="
      ${zazaVenv}/bin/python3 -c "
import pathlib, sys, re

def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()

# Collect core venv package names
core = set()
venv_sp = pathlib.Path('${zazaVenv}/${sitePackagesPath}')
for di in venv_sp.glob('*.dist-info'):
    meta = di / 'METADATA'
    if meta.exists():
        for line in meta.read_text().splitlines():
            if line.startswith('Name:'):
                core.add(canonical(line.split(':', 1)[1].strip()))
                break

# Check each extra package for collisions
extras_dirs = [${lib.concatMapStringsSep ", " (p: "'${toString p}'") allExtraPythonPackages}]
for edir in extras_dirs:
    sp = pathlib.Path(edir) / '${sitePackagesPath}'
    if not sp.exists():
        continue
    for di in sp.glob('*.dist-info'):
        meta = di / 'METADATA'
        if not meta.exists():
            continue
        for line in meta.read_text().splitlines():
            if line.startswith('Name:'):
                pkg = canonical(line.split(':', 1)[1].strip())
                if pkg in core:
                    print(f'ERROR: plugin package \"{pkg}\" collides with a package in zaza sealed venv', file=sys.stderr)
                    print(f'  from: {di}', file=sys.stderr)
                    print(f'  Remove this dependency from extraPythonPackages.', file=sys.stderr)
                    sys.exit(1)
                break

print('No collisions found.')
      "
      echo "=== No collisions ==="
    ''}

    runHook postInstall
  '';

  passthru = {
    inherit zazaTui zazaWeb zazaNpmLib zazaVenv;

    devShellHook = ''
      STAMP=".nix-stamps/agent-zaza"
      STAMP_VALUE="${pyprojectHash}:${uvLockHash}"
      if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$STAMP_VALUE" ]; then
        echo "agent-zaza: installing Python dependencies..."
        uv venv .venv --python ${python312}/bin/python3 2>/dev/null || true
        source .venv/bin/activate
        uv pip install -e ".[all]"
        [ -d mini-swe-agent ] && uv pip install -e ./mini-swe-agent 2>/dev/null || true
        [ -d tinker-atropos ] && uv pip install -e ./tinker-atropos 2>/dev/null || true
        mkdir -p .nix-stamps
        echo "$STAMP_VALUE" > "$STAMP"
      else
        source .venv/bin/activate
        export ZAZA_PYTHON=${zazaVenv}/bin/python3
      fi
    '';
  };

  meta = with lib; {
    description = "AI agent with advanced tool-calling capabilities";
    homepage = "https://github.com/NousResearch/agent-zaza";
    mainProgram = "zaza";
    license = licenses.mit;
    platforms = platforms.unix;
  };
}
