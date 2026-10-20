"""Logging setup shared by the command-line scripts."""
import logging


class _ConsoleFormatter(logging.Formatter):
    """Plain text for INFO (the normal program output); a level prefix for the rest."""

    def format(self, record):
        msg = super().format(record)
        return msg if record.levelno == logging.INFO else f"{record.levelname.lower()}: {msg}"


def add_arguments(parser):
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug messages")


def setup(args):
    level = logging.DEBUG if args.verbose else logging.INFO
    handler = logging.StreamHandler()
    handler.setFormatter(_ConsoleFormatter("%(message)s"))
    logging.basicConfig(level=level, handlers=[handler])
    # Keep third-party debug output (matplotlib font manager, PIL...) out of --verbose.
    for name in ("matplotlib", "PIL"):
        logging.getLogger(name).setLevel(logging.WARNING)
