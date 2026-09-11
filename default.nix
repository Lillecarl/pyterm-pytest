# The package this repository builds. The suite that judges it lives in
# `nix/checks.nix`, which declares its own inputs.
#
# **The ceiling is the design.** This package takes the floor the
# widgets take -- `pyte` and `ptyhost` -- and never a layer above it,
# so every repository's checks can take it as an input without a
# cycle. No module needs either yet: the first one that does is the
# driver, and the arguments arrive with it. README.md says the rest.
{
  lib,
  buildPythonPackage,
  setuptools,
  callPackage,
  runCommand,
  wlroots,
  wayland-scanner,
  coreutils,
  python,
}:
let
  # The bindings the wayland seat's keyboard holder runs, generated
  # from the protocol XMLs at build time. pywayland ships no wlr
  # protocols, and its scanner cannot resolve a lone extension XML:
  # the core namespace has to be in the same scan for `wl_seat` to
  # resolve.
  waylandProtocols = runCommand "pyterm-pytest-wayland-protocols" {
    nativeBuildInputs = [ (python.withPackages (ps: [ ps.pywayland ])) ];
    # The scanner wants pkg-config to find the core XML, which the
    # command line names anyway, so the lookup is stopped instead of
    # satisfied.
    PKG_CONFIG = "${coreutils}/bin/false";
  } ''
    python -m pywayland.scanner -o $out/protocols -i \
      ${wayland-scanner}/share/wayland/wayland.xml \
      ${wlroots.src}/protocol/virtual-keyboard-unstable-v1.xml \
      ${wlroots.src}/protocol/wlr-screencopy-unstable-v1.xml
    touch $out/protocols/__init__.py
  '';

  package = buildPythonPackage {
    pname = "pyterm-pytest";
    version = "0.1";
    src = lib.cleanSource ./.;
    pyproject = true;

    # Only ruff configuration lives in pyproject.toml, so the build
    # backend has to be named here rather than read from it.
    build-system = [ setuptools ];
    dependencies = [ ];

    # The suite runs as `checks.unit`, against the installed package.
    doCheck = false;
    pythonImportsCheck = [ "pyterm_pytest" ];

    passthru = { inherit checks waylandProtocols; };

    meta = {
      description = "Test equipment for the pyterm collection: seats, drivers, budgets";
      homepage = "https://github.com/Lillecarl/pyterm-pytest";
      license = lib.licenses.bsd3;
    };
  };

  # Only the tests, not the whole repository. A copy of everything makes the
  # test runs rebuild on every unrelated edit.
  #
  # `pyproject.toml` comes with them: pytest reads its settings from the root
  # it finds, and a root with no config file is a root with no settings.
  testSources = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./tests
      ./pyproject.toml
    ];
  };

  checks = callPackage ./nix/checks.nix {
    inherit package testSources waylandProtocols;
    # There is no top-level `pywayland`; the python that builds this
    # package carries its own.
    pywayland = python.pkgs.pywayland;
  };
in
package
