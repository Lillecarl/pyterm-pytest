#!/usr/bin/env python
import os

from setuptools import find_packages, setup

with open(os.path.join(os.path.dirname(__file__), "README.md")) as f:
    long_description = f.read()

# The floor is per module: most of this package imports the standard
# library alone, the driver takes `ptyhost` when it lands, and nothing
# imports a layer above that. README.md says why.
requirements = []


setup(
    name="pyterm-pytest",
    version="0.1",
    license="LICENSE",
    url="https://github.com/Lillecarl/pyterm-pytest",
    description="Test equipment for the pyterm collection: seats, drivers, budgets.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages("."),
    install_requires=requirements,
    package_data={"pyterm_pytest": ["py.typed"]},
    python_requires=">=3.10",
)
