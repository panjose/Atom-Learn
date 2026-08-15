from __future__ import annotations

from pathlib import Path

import pytest


FAST_FILES = {
    "test_cli_contract.py",
    "test_documentation.py",
    "test_self_evolution_contracts.py",
    "test_manager_contracts.py",
    "test_release_properties.py",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Keep a small deterministic contract suite separate from workflow integration tests."""
    for item in items:
        filename = Path(str(item.fspath)).name
        item.add_marker(pytest.mark.fast if filename in FAST_FILES else pytest.mark.integration)
