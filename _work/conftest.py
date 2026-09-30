"""Shared pytest bootstrap for the ``_work`` regression suite.

The 28 ``test_*.py`` files began life as standalone scripts: most of them add
``_work/ha_stub`` / ``_work/pylibs`` / the project root to ``sys.path`` by hand
and drive themselves with ``asyncio.run``.  There is no ``pytest.ini`` or
``pyproject.toml``, so a bare ``pytest test_*.py`` used to break in three ways:

* five files lacked the ``sys.path`` bootstrap and could not import;
* two files called ``sys.exit(...)`` at module top level, aborting collection
  with ``INTERNALERROR``;
* ``async def`` tests were reported as *"async def functions are not natively
  supported"* because pytest-asyncio was not configured.

On top of that, collection shares one interpreter and one ``sys.path``.  Some
scripts prepend only ``ha_stub`` (which carries a tiny ``voluptuous`` shim)
while others need the real vendored copy under ``pylibs``.  Whichever import
happens first wins in ``sys.modules``, so the suite pre-imports the real
library here, before any test module can shadow it.

This conftest supplies the missing pieces so the whole set can run under a
single pytest invocation while the files stay runnable as scripts.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Same search path every standalone test builds for itself: HA stand-in,
# vendored pure-python libs, then the repository root for custom_components.
# Inserted in reverse so the final order is [ROOT, pylibs, ha_stub] -- the
# real voluptuous in pylibs must beat the minimal ha_stub shim.
for _path in (os.path.join(HERE, "ha_stub"), os.path.join(HERE, "pylibs"), ROOT):
    while _path in sys.path:
        sys.path.remove(_path)
    sys.path.insert(0, _path)

# Cache the real voluptuous before collection starts.  Some scripts prepend
# ha_stub again at import time; without this they would install the stub for
# every module collected afterwards.
import voluptuous  # noqa: E402,F401

# pytest-asyncio is installed in the runner venv; make the dependency explicit
# so an unfinished environment fails loudly instead of silently skipping the
# coroutine tests.
pytest_plugins = ("pytest_asyncio",)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # pytest-asyncio resolves the mode as: --asyncio-mode option, then the
    # ``asyncio_mode`` ini value.  There is no ini file in _work, so pin the
    # option here; ``tryfirst`` makes it apply before pytest-asyncio's own
    # pytest_configure/report-header run.
    config.option.asyncio_mode = "auto"
