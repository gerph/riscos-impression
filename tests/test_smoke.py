"""Smoke tests confirming the package installs and the CLI runs."""

from riscos_impression import __version__
from riscos_impression.cli import main


def test_version_is_set():
    assert __version__


def test_cli_runs(capsys):
    assert main([]) == 0
    assert "riscos-impression" in capsys.readouterr().out
