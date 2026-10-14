# pyterm-pytest

> This repository is a copy. Work on pyterm-pytest happens in
> [pyterm](https://github.com/Lillecarl/pyterm), which exports its `pyterm-pytest/`
> directory here on every push. Send changes there, and issues to
> [Lillecarl/pymux](https://github.com/Lillecarl/pymux/issues).
> The docs are at <https://lillecarl.github.io/pyterm-pytest/>.

<!-- The lines above are pyterm's nix/shared/readme-head.md, written by sync-sources. -->

The test equipment of the pyterm collection, shared by the repositories
it serves.

Each repository writes its own tests and keeps them beside the code
they judge. This package holds what those tests stand on when several
of them need the same thing:

* **Seats** -- a real terminal on a display server of its own: Xvfb for
  xterm, a kiosk compositor for foot and kitty, and how to photograph
  either. Lillecarl/pymux#276. A seat starts each terminal once per run, with
  the `ptyproxy` viewer in it, and runs each command through the
  proxy. Between commands it resets the terminal and checks the
  picture against the terminal's first blank one. Lillecarl/pymux#591.
  `Seat.twins` shows an application in two kept terminals at once,
  bare in one and through the program under test in the other, and
  hands a director a `Twins` to press keys, wait for text and take a
  picture of each. Shared, one run feeds both and the proxy answers
  the queries; `apart`, each terminal runs and answers a copy of its
  own. Each terminal's replies are kept either way.
  Lillecarl/pymux#592, Lillecarl/pymux#593.
* **Drivers** -- run a program on a pty, press keys at it, and fence
  the moment its work is done. Built on `ptyhost`, never around it.
  Lillecarl/pymux#275. `ptyproxy` is the first: the test runs the
  application and serves its bytes on a socket to any number of
  terminals, each running the `ptyproxy` viewer, and parses the same
  bytes into a pyte screen a scenario reads. Lillecarl/pymux#590.
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
the way `pymux/nix/checks.nix` takes `ptterm`, and its suite runs with
the package in its test environment, beside `pytest`. The nixpkgs
precedents are `dejagnu`, which is test equipment depending on the
`expect` it drives with, and `pytest-xvfb`, which is the X seat as a
pytest fixture.

<!-- The lines below are pyterm's nix/shared/readme-tail.md, written by sync-sources. -->

## Building

    nix build github:Lillecarl/pyterm-pytest    # from this repository alone
    nix build --file . pyterm-pytest            # in a checkout of pyterm

The suites that judge it run in pyterm, as `checks.pyterm-pytest-*`.

## The collection

pyterm-pytest is one source of [pyterm](https://github.com/Lillecarl/pyterm), and
each source claims one job:

| source | job |
| --- | --- |
| [pyte](https://lillecarl.github.io/pyte/) | parse, and hold a screen |
| [ptyhost](https://lillecarl.github.io/ptyhost/) | run a program on a pty, and carry its bytes |
| [ptterm](https://lillecarl.github.io/ptterm/) | draw one terminal, with prompt-toolkit |
| [txterm](https://lillecarl.github.io/txterm/) | draw one terminal, with Textual |
| [pymux](https://lillecarl.github.io/pymux/) | arrange several of them |
| [pysmithay](https://lillecarl.github.io/pysmithay/) | a headless Wayland compositor, for a GUI program in a pane |
| [pyterm-pytest](https://lillecarl.github.io/pyterm-pytest/) | the test equipment the suites share |

[pyterm's site](https://lillecarl.github.io/pyterm/) holds every source's
docs.
