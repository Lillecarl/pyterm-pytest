"""
The test equipment of the pyterm collection, shared by its repositories.

Each module carries the floor it needs and no more. The ones that read
lists and count instructions import the standard library alone; the
driver imports `ptyhost` when it lands. No module here imports a layer
above `ptyhost`: `ptterm`, `txterm`, `prompt_toolkit` and `pymux` are
the layers this package serves, and a rig that imported one could no
longer be an input to its checks. `pyterm_pytest.tests.test_the_ceiling`
holds that rule where a change would trip it.
"""
