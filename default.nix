# pyterm-pytest on its own. Work on it happens in pyterm
# (https://github.com/Lillecarl/pyterm), the repository this one is exported
# from; this file builds this directory against its siblings there, at the
# pyterm commit `flake.lock` names.
let
  compat = import ./nix/compat.nix;
in
{
  inputs ? compat.inputs,
  system ? builtins.currentSystem,
  # Your own nixpkgs, or null for the one pyterm pins.
  pkgs ? null,
}:
let
  pyterm = import inputs.pyterm (
    {
      inherit (inputs.pyterm) inputs;
      inherit system;
      sourceOverrides.pyterm-pytest = ./.;
    }
    // (if pkgs == null then { } else { inherit pkgs; })
  );
in
{
  inherit (pyterm) pyterm-pytest shell;
  default = pyterm.pyterm-pytest;
  checks = pyterm.pyterm-pytest.checks;
  pages = pyterm.pages.pyterm-pytest;
}
