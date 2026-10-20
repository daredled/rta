#!/usr/bin/env python3
"""Car audio tuning tools: test signals, real-time analyzer and time alignment.

Run "main.py <command> --help" for each command's options.
"""
import argparse
import logging
import sys

import delay
import gen_noise
import rta

log = logging.getLogger(__name__)

COMMANDS = {"generate": gen_noise, "rta": rta, "delay": delay}


class ConsoleFormatter(logging.Formatter):
    """Plain text for INFO (the normal program output); a level prefix for the rest."""

    def format(self, record):
        msg = super().format(record)
        return msg if record.levelno == logging.INFO else f"{record.levelname.lower()}: {msg}"


def setup_logging(verbose):
    handler = logging.StreamHandler()
    handler.setFormatter(ConsoleFormatter("%(message)s"))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=[handler])
    # Keep third-party debug output (matplotlib font manager, PIL...) out of --verbose.
    for name in ("matplotlib", "PIL"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True, metavar="command")
    for name, module in COMMANDS.items():
        sp = sub.add_parser(name, help=module.__doc__.splitlines()[0], description=module.__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
        module.add_arguments(sp)
        sp.add_argument("-v", "--verbose", action="store_true", help="show debug messages")
        sp.set_defaults(run=module.run)
    args = p.parse_args(argv)
    setup_logging(args.verbose)

    try:
        return args.run(args) or 0
    except (ValueError, OSError) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
