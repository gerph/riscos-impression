"""Smoke tests confirming the package installs and the CLI runs."""

from riscos_impression import __version__
from riscos_impression.cli import main


def test_version_is_set():
    assert __version__


def test_cli_runs(capsys):
    # No subcommand given: prints usage and reports a non-zero exit,
    # now that the real `convert` subcommand exists (see test_cli.py
    # for the actual conversion behaviour).
    assert main([]) == 1
    assert "riscos-impression" in capsys.readouterr().out
