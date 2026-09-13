The test equipment of the pyterm collection, shared by the repositories
it serves.

Each repository writes its own tests and keeps them beside the code
they judge. This package holds what those tests stand on when several
of them need the same thing:

* **Seats** -- a real terminal on a display server of its own: Xvfb for
  xterm, a kiosk compositor for foot and kitty, and how to photograph
  either. It moves here from `pymux/tests/take_picture.py`.
  Lillecarl/pymux#276.
* **Drivers** -- run a program on a pty, press keys at it, and fence
  the moment its work is done. Built on `ptyhost`, never around it.
  Lillecarl/pymux#275.
* **Budgets and recorded lists** -- read a name-to-count file, judge a
  run against one, and complain at a difference in either direction.
  Three copies of that reader exist in the collection today.
  Lillecarl/pymux#277.

## The ceiling

The package takes the floor the widgets take -- `pyte` and `ptyhost` --
and never a layer above it. `ptterm`, `txterm`, `prompt_toolkit` and
`pymux` are the layers this package serves: a module that imported one
could no longer be an input to that layer's checks.
`tests/test_the_ceiling.py` holds the rule on every module, on every
run. A module that needs none of the floor imports the standard
library alone, so a repository whose own floor is lower takes only the
modules it can.

Each repository's `nix/checks.nix` takes this package as an argument,
the way `pymux/nix/checks.nix` takes `ptterm` today, and its suite
runs with the package in `pythonWithTests`, where `pytest` and
`hypothesis` already are. The nixpkgs precedents are `dejagnu`, which
is test equipment depending on the `expect` it drives with, and
`pytest-xvfb`, which is the X seat as a pytest fixture.

## Building

    nix build --file . checks.pyterm-pytest-unit
