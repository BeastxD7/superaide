"""Superaide: an AI hire that grows up like a child, with identity checks you can trust."""

from superaide import _compat

_compat.install()


def main() -> None:
    from superaide.cli import main as cli_main

    cli_main()
