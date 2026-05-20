# nix/web.nix — ZAZA Web Dashboard (Vite/React) frontend build
{ pkgs, zazaNpmLib, ... }:
let
  src = ../web;
  npmDeps = pkgs.fetchNpmDeps {
    inherit src;
    hash = "sha256-HWB1piIPglTXbzQHXFYHLgVZIbDb60esupXSQGa1+lI=";
  };

  npm = zazaNpmLib.mkNpmPassthru { folder = "web"; attr = "web"; pname = "zaza-web"; };
in
pkgs.buildNpmPackage (npm // {
  pname = "zaza-web";
  version = "0.0.0";
  inherit src npmDeps;

  doCheck = false;

  buildPhase = ''
    npx tsc -b
    npx vite build --outDir dist
  '';

  installPhase = ''
    runHook preInstall
    cp -r dist $out
    runHook postInstall
  '';
})
