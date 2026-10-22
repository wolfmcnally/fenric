"""Fenric: native dialogs for agents and scripts.

A caller sends one spec (a verb and its parameters), the person at the
machine interacts with one window, and the caller reads one result. The
``fenric`` command and this library share one engine:

    from fenric import run

    result = run({"verb": "confirm", "message": "Proceed?"})
    if result.status == "ok" and result.value:
        ...
"""

from importlib.metadata import version

from fenric.engine import Result, run
from fenric.gallery import load_gallery
from fenric.interaction import IntegratedResult, run_integrated

# The version is the installed distribution's, set once in pyproject.toml.
__version__ = version("fenric")

__all__ = ["IntegratedResult", "Result", "__version__", "load_gallery", "run", "run_integrated"]
