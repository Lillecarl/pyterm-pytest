# The suite that judges pyterm-pytest.
#
# It declares its own inputs, so `default.nix` holds the package and carries
# nothing that only a test needs.
#
# `testEnv` and `testSources` come from `default.nix`: the first because a
# suite runs against the installed package, the second because it knows where
# the repository root is and this file does not.
#
# The real judges of this package are the suites that use it, in the other
# repositories. What runs here is the rule nothing else holds: the ceiling.
# `tests/test_the_ceiling.py` says what it is.
#
# `nix/suite.nix` says why a check is two derivations.
{
  # The python the suite runs on: a virtualenv of pyterm-pytest, what it
  # declares, and its `test` extra. That extra carries pywayland, because
  # the seat's keyboard holder imports it and the ceiling imports every
  # module: what a module imports has to be on the path of the suite that
  # judges it. `default.nix` builds this from `pyproject.toml`, so there is
  # no second list here. Lillecarl/pymux#319.
  testEnv,
  callPackage,
  testSources,
  waylandProtocols,
  # `ps`, which reads a viewer's argv[0] the way a multiplexer does.
  procps,
  ruff,
}:
let
  inherit (callPackage ./suite.nix { }) suite;

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
    inputs = [
      testEnv
      procps
    ];
    env = { inherit selection; };
    setup = prepare;
  } "python -m pytest $selection -q -p no:cacheprovider";

  # The selected rules, the layout at width 120, and the future import in
  # every file. `ruff.toml` in the pyterm root says what each rule is for.
  # The package stays out of `prepare`: beside the tests it would shadow
  # the installed one.
  ruff = suite {
    name = "pyterm-pytest-ruff";
    inputs = [ ruff ];
    setup = prepare + ''
      cp -r ${testSources}/pyterm_pytest .
    '';
  } ''
    export RUFF_CACHE_DIR="$TMPDIR/ruff"
    ruff check pyterm_pytest tests
    ruff format --check pyterm_pytest tests
    missing=$(grep -rL '^from __future__ import annotations' --include='*.py' --exclude-dir='.*' --exclude-dir='__pycache__' pyterm_pytest tests || true)
    if [ -n "$missing" ]; then
      echo "files without the future import:"
      echo "$missing"
      exit 1
    fi
  '';
}
