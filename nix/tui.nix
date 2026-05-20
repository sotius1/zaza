# nix/tui.nix — ZAZA TUI (Ink/React) compiled with tsc and bundled
{ pkgs, zazaNpmLib, ... }:
let
  src = ../ui-tui;
  npmDeps = pkgs.fetchNpmDeps {
    inherit src;
    hash = "sha256-a/HGI9OgVcTnZrMXA7xFMGnFoVxyHe95fulVz+WNYB0=";
  };

  npm = zazaNpmLib.mkNpmPassthru { folder = "ui-tui"; attr = "tui"; pname = "zaza-tui"; };

  packageJson = builtins.fromJSON (builtins.readFile (src + "/package.json"));
  version = packageJson.version;
in
pkgs.buildNpmPackage (npm // {
  pname = "zaza-tui";
  inherit src npmDeps version;

  doCheck = false;
  npmFlags = [ "--legacy-peer-deps" ];

  installPhase = ''
    runHook preInstall

    mkdir -p $out/lib/zaza-tui

    cp -r dist $out/lib/zaza-tui/dist

    # runtime node_modules
    cp -r node_modules $out/lib/zaza-tui/node_modules

    # @zaza/ink is a file: dependency, we need to copy it in fr
    rm -f $out/lib/zaza-tui/node_modules/@zaza/ink
    cp -r packages/zaza-ink $out/lib/zaza-tui/node_modules/@zaza/ink

    # package.json needed for "type": "module" resolution
    cp package.json $out/lib/zaza-tui/

    runHook postInstall
  '';
})
