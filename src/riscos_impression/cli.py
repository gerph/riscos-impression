"""Command-line entry point for riscos-impression.

The ``convert`` subcommand is added once a document model and at least one
output converter exist; see PLAN.md.
"""

import argparse
import sys

from riscos_impression import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="riscos-impression")
    parser.add_argument("--version", action="version", version=__version__)
    parser.parse_args(argv)
    print("riscos-impression: no subcommands are implemented yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
