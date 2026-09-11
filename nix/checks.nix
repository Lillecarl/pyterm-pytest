# The suite that judges pyterm-pytest.
#
# It declares its own inputs, so `default.nix` holds the package and carries
# nothing that only a test needs.
#
# `package` and `testSources` come from `default.nix`: the first because a
# suite runs against the installed package, the second because it knows where
# the repository root is and this file does not.
#
# The real judges of this package are the suites that use it, in the other
# repositories. What runs here is the rule nothing else holds: the ceiling.
# `tests/test_the_ceiling.py` says what it is.
#
# `nix/suite.nix` says why a check is two derivations.
{
  python,
  pytest,
  callPackage,
  package,
  testSources,
  pywayland,
  waylandProtocols,
}:
let
  inherit (callPackage ./suite.nix { }) suite;

  # pywayland is here because the seat's keyboard holder imports it,
  # and the ceiling imports every module: what a module imports has to
  # be on the path of the suite that judges it.
  pythonWithTests = python.withPackages (ps: [
    package
    pytest
    pywayland
  ]);

  # Narrow a run to one file or one test while hunting:
  #
  #     PYTERM_PYTEST_TESTS=tests/test_the_ceiling.py \
  #       nix build --file . checks.pyterm-pytest-unit
  selection = builtins.getEnv "PYTERM_PYTEST_TESTS";

  prepare = ''
    cp -r ${testSources}/tests .
    cp ${testSources}/pyproject.toml .
    chmod -R +w .
    export HOME="$TMPDIR"
    export LANG=C.UTF-8
    export PYTHONDONTWRITEBYTECODE=1
    # The holder's generated bindings, for the module that imports
    # them; the runtime variable the seat reads carries the same.
    export PYTHONPATH=${waylandProtocols}
    export PYTERM_WAYLAND_PROTOCOLS=${waylandProtocols}
  '';
in
{
  unit = suite {
    name = "pyterm-pytest-unit";
    inputs = [ pythonWithTests ];
    env = { inherit selection; };
    setup = prepare;
  } "python -m pytest $selection -q -p no:cacheprovider";
}
