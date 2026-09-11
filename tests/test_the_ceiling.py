"""
The ceiling: what no module of this package may import.

`ptterm`, `txterm`, `prompt_toolkit` and `pymux` are the layers this
package serves. A module that imported one could not be an input to
that layer's checks, so the rule is held here, on every module, on
every run. `ptyhost` and `pyte` are the floor, and they are allowed:
the rig sits between them and the layers it serves.
"""

import importlib
import pkgutil
import sys

import pyterm_pytest

FORBIDDEN = ("ptterm", "txterm", "pymux", "prompt_toolkit")


def test_the_ceiling():
    "Every module imports, and none of them reaches a layer it serves."
    before = set(sys.modules)
    for module in pkgutil.walk_packages(pyterm_pytest.__path__, "pyterm_pytest."):
        importlib.import_module(module.name)
    held = set(sys.modules) - before
    offenders = sorted(name for name in held if name.split(".")[0] in FORBIDDEN)
    assert not offenders, offenders
