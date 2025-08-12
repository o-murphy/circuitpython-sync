import argparse
import sys
from pathlib import Path

from circuitpython_sync import Client, Device, DEFAULT_CACHE_PATH, DEFAULT_URL, DEFAULT_PASS


def main(args=None):
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("-u", "--url", type=str, help="device web workflow url", default=DEFAULT_URL)
    common_parser.add_argument("-p", "--password", type=str, help="device web workflow password", default=DEFAULT_PASS)

    ap = argparse.ArgumentParser(description=__doc__, parents=[common_parser])
    subparsers = ap.add_subparsers(dest="command", help="available commands", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="fetches data from the device", parents=[common_parser])
    fetch_parser.add_argument("--dst", type=Path, help="destination path", default=DEFAULT_CACHE_PATH)

    sync_parser = subparsers.add_parser("sync", help="syncs data to the device", parents=[common_parser])
    sync_parser.add_argument("--src", type=Path, help="source path", default=DEFAULT_CACHE_PATH)

    repl_parser = subparsers.add_parser("repl", help="REPL", parents=[common_parser])

    ns = ap.parse_args(args or sys.argv[1:])

    with Client(ns.url, ns.password) as client:
        if ns.command == "fetch":
            Device(client, ns.dst).fetch()
        elif ns.command == "sync":
            Device(client, ns.src).sync()
        elif ns.command == "repl":
            client.repl_ws()


if __name__ == "__main__":
    main(sys.argv[1:])
