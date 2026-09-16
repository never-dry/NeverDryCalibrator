"""Test fixtures and the two import paths this suite needs.

The calibration domain must be testable without a Home Assistant runtime, which
is the whole point of keeping ``model/`` free of Home Assistant imports. To make
that literally true, the component directory is put on ``sys.path`` so the domain
can be imported as a plain ``model`` package, with no parent package to trigger.
The Home Assistant fixtures are wired only when
``pytest-homeassistant-custom-component`` is installed, so the domain tests still
run in a bare environment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = REPO_ROOT / "custom_components" / "neverdry_calibrator"

for path in (str(REPO_ROOT), str(COMPONENT_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

try:  # pragma: no cover - import guard
    import pytest_homeassistant_custom_component  # noqa: F401

    HAS_HOME_ASSISTANT = True
except ImportError:  # pragma: no cover
    HAS_HOME_ASSISTANT = False


if HAS_HOME_ASSISTANT:

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Let Home Assistant load the custom integration during tests."""
        yield
