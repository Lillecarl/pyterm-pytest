# The package this repository builds. The suite that judges it lives in
# `nix/checks.nix`, which declares its own inputs.
#
# **The ceiling is the design.** This package takes the floor the
# widgets take -- `pyte` and `ptyhost` -- and never a layer above it,
# so every repository's checks can take it as an input without a
# cycle. No module needs either yet: the first one that does is the
# driver, and the arguments arrive with it. README.md says the rest.
#
# **This is a pyproject.nix builders package, not a nixpkgs one.** What it
# needs is declared in `pyproject.toml` and the renderer reads it; an
# environment is a virtualenv rather than a PYTHONPATH. Lillecarl/pymux#319.
{
  lib,
  stdenv,
  python,
  pyprojectHook,
  resolveBuildSystem,
  mkVirtualEnv,
  mkProject,
  callPackage,
  runCommand,
  wlroots,
  wayland-scanner,
  coreutils,
}:
let
  # The bindings the wayland seat's keyboard holder runs, generated
  # from the protocol XMLs at build time. pywayland ships no wlr
  # protocols, and its scanner cannot resolve a lone extension XML:
  # the core namespace has to be in the same scan for `wl_seat` to
  # resolve.
  #
  # The scanner runs on a nixpkgs environment and not on this set. It is a
  # tool that writes files, reaches no closure that runs, and would
  # otherwise need a virtualenv of a package this one does not depend on.
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

  # What the wheel is built from, and nothing else. A denylist would carry
  # `tests` and the `__pycache__` beside every module, and a source that a
  # test run changes rebuilds every repository that takes this one.
  # Lillecarl/pymux#320.
  projectRoot = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      # Not only the `.py` files: `py.typed` is what tells a checker that
      # the annotations here are meant to be read. `setup.py` named it in
      # `package_data`, and hatchling takes the whole directory.
      (lib.fileset.fileFilter (
        file: file.hasExt "py" || file.name == "py.typed"
      ) ./pyterm_pytest)
      ./pyproject.toml
      ./README.md
      ./LICENSE
    ];
  };

  package =
    (mkProject {
      inherit projectRoot python;
      extra = rendered: {
        # The generated bindings ride on the package, the way ptterm's
        # conformance suites do: a tool is not a suite, and pymux's checks
        # take this one from here.
        passthru = rendered.passthru // { inherit checks waylandProtocols; };

        meta = rendered.meta // {
          description = "Test equipment for the pyterm collection: seats, drivers, budgets";
          homepage = "https://github.com/Lillecarl/pyterm-pytest";
          license = lib.licenses.bsd3;
        };
      };
    })
      {
        inherit stdenv pyprojectHook resolveBuildSystem;
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

  # What the suite runs on: pyterm-pytest, what it declares, and the `test`
  # extra beside them in the same file -- which is where pywayland is, and
  # `pyproject.toml` says why it is there and not in `dependencies`.
  testEnv = mkVirtualEnv "pyterm-pytest-test-env" { pyterm-pytest = [ "test" ]; };

  checks = callPackage ./nix/checks.nix {
    inherit testEnv testSources waylandProtocols;
  };
in
package
