# nix/packages.nix — Agent ZAZA package built with uv2nix
{ inputs, ... }:
{
  perSystem =
    { pkgs, inputs', ... }:
    let
      zazaAgent = pkgs.callPackage ./agent-zaza.nix {
        inherit (inputs) uv2nix pyproject-nix pyproject-build-systems;
        npm-lockfile-fix = inputs'.npm-lockfile-fix.packages.default;
        # Only embed clean revs — dirtyRev doesn't represent any upstream
        # commit, so comparing it would always claim "update available".
        rev = inputs.self.rev or null;
      };
    in
    {
      packages = {
        default = zazaAgent;
        tui = zazaAgent.zazaTui;
        web = zazaAgent.zazaWeb;

        fix-lockfiles = zazaAgent.zazaNpmLib.mkFixLockfiles {
          packages = [ zazaAgent.zazaTui zazaAgent.zazaWeb ];
        };
      };
    };
}
