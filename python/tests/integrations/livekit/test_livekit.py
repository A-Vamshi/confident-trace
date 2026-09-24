"""Run LiveKit checks without importing or configuring it in pytest's process."""

import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest
from conftest import ROOT

HERE = Path(__file__).parent


@pytest.fixture
def livekit_environment():
    for package in ("livekit-agents", "livekit-plugins-openai"):
        try:
            version(package)
        except PackageNotFoundError:
            pytest.skip(f"{package} is not installed")
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "HOME",
        "USERPROFILE",
        "TMP",
        "TEMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(PYTHONPATH=str(ROOT / "python/src"), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    return env


@pytest.mark.parametrize(
    "scenario",
    [
        "test_session_spans_are_labelled_and_llm_calls_appear_once",
        "test_unconfigured_livekit_tracer_uses_our_provider",
        "test_configured_livekit_tracer_is_preserved",
        "test_unselected_livekit_keeps_provider_spans",
    ],
)
def test_livekit_scenario(scenario, livekit_environment, tmp_path):
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "pytest_asyncio.plugin",
            f"{HERE / 'livekit_scenarios.py'}::{scenario}",
        ],
        env=livekit_environment,
        cwd=tmp_path,
        check=True,
        timeout=60,
    )
