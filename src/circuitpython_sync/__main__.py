import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from circuitpython_sync import (
    Client,
    Device,
    DEFAULT_CACHE_PATH,
    DEFAULT_URL,
    DEFAULT_PASS,
    ptree,
    Repl,
)


def main(args=None):
    """
    Main entry point for the command-line tool.
    """
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "-u",
        "--url",
        type=str,
        help="URL of the CircuitPython device's web workflow (e.g., http://circuitpython.local/)",
        default=DEFAULT_URL
    )
    common_parser.add_argument(
        "-p",
        "--password",
        type=str,
        help="Password for the CircuitPython device's web workflow.",
        default=DEFAULT_PASS
    )

    ap = argparse.ArgumentParser(
        description="A command-line tool for managing files on a CircuitPython device via the web workflow.",
        parents=[common_parser]
    )
    subparsers = ap.add_subparsers(
        dest="command",
        help="Choose a command to execute.",
        required=True
    )

    # Parser for the 'pull' command
    pull_parser = subparsers.add_parser(
        "pull",
        help="Downloads files and directories from the device's file system to a local cache.",
        parents=[common_parser]
    )
    pull_parser.add_argument(
        "--dst",
        type=Path,
        help="Local destination path to cache the device's file system.",
        default=DEFAULT_CACHE_PATH
    )

    # Parser for the 'push' command
    push_parser = subparsers.add_parser(
        "push",
        help="Uploads files and directories from a local path to the device's file system.",
        parents=[common_parser]
    )
    push_parser.add_argument(
        "--src",
        type=Path,
        help="Local source path containing files to push to the device.",
        default=DEFAULT_CACHE_PATH
    )

    # Parser for the 'tree' command
    tree_parser = subparsers.add_parser(
        "tree",
        help="Displays the file system tree of the CircuitPython device.",
        parents=[common_parser]
    )
    tree_parser.add_argument(
        "--path",
        type=Path,
        help="The starting path on the device to display the tree from.",
        default="fs/"
    )

    # Parser for the 'repl' command
    repl_parser = subparsers.add_parser(
        "repl",
        help="Connects to the device's serial REPL over WebSocket.",
        parents=[common_parser]
    )

    ns = ap.parse_args(args or sys.argv[1:])

    try:
        with Client(ns.url, ns.password) as client:
            if ns.command == "pull":
                Device(client, ns.dst).pull()
            elif ns.command == "push":
                Device(client, ns.src).push()
            elif ns.command == "tree":
                with tempfile.TemporaryDirectory() as tmpdir:
                    ptree(Device(client, Path(tmpdir)).tree(ns.path))
            elif ns.command == "repl":
                asyncio.run(Repl(client).run_repl_ws())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
