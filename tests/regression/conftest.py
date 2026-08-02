from __future__ import annotations

import importlib
import shutil
from pathlib import Path

import pytest


FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "fixtures"
    / "regression"
)


@pytest.fixture
def regression_project(tmp_path: Path):
    def copy(name: str) -> Path:
        target = tmp_path / name
        shutil.copytree(FIXTURES / name, target)
        return target

    return copy


@pytest.fixture
def regression_api():
    def load(module_name: str, attribute: str, defect: str):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            pytest.fail(defect)
        value = getattr(module, attribute, None)
        if not callable(value):
            pytest.fail(defect)
        return value

    return load
