import json
import os
import shutil
import threading
import webbrowser
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin

import requests
import websocket

DEFAULT_URL = "http://circuitpython.local/"
DEFAULT_PASS = "passw0rd"
DEFAULT_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
DEFAULT_KWARGS = {"allow_redirects": True, "timeout": 5}
DEFAULT_CACHE_PATH = "CircuitPython"

# ANSI color codes
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


class ClientRequestError(Exception):
    pass


class UnknownCircuitPythonDevice(Exception):
    pass


def request_exception_wrapper(func):
    """
    Decorator to wrap requests with a custom exception for better error handling.
    """

    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except requests.RequestException as e:
            try:
                # Attempt to get the response text for more details
                raise ClientRequestError(f"{e}: {e.response.text}") from e
            except AttributeError:
                # If no response text is available, raise a simpler error
                raise ClientRequestError(f"{e}") from e

    return wrapper


class Client:
    """
    Client for interacting with a CircuitPython device via its web workflow.
    """

    def __init__(self, url=DEFAULT_URL, password=DEFAULT_PASS, headers=None, **kwargs):
        if not url.endswith("/"):
            url += "/"
        self._url = url
        self._headers = headers or DEFAULT_HEADERS
        self._auth = ("", password)
        self._kwargs = {
            "auth": self._auth,
        }
        self._kwargs.update()
        self._kwargs.update(kwargs)

        self._ws = None
        self._ws_thread = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close_repl_ws()
        return False

    @request_exception_wrapper
    def options(self):
        """Send an OPTIONS request to the device."""
        resp = requests.options(urljoin(self._url, "fs/"), **self._kwargs)
        resp.raise_for_status()
        return resp

    @request_exception_wrapper
    def get(self, path):
        """Send a GET request to the device to retrieve data."""
        resp = requests.get(
            urljoin(self._url, path), headers=self._headers, **self._kwargs
        )
        resp.raise_for_status()
        return resp

    @request_exception_wrapper
    def put(self, path, data=None):
        """Send a PUT request to the device to create/update a file or directory."""
        resp = requests.put(
            urljoin(self._url, path), data=data, headers=self._headers, **self._kwargs
        )
        resp.raise_for_status()
        return resp

    @request_exception_wrapper
    def move(self, src_path, dest_path):
        """Send a MOVE request to the device to rename/move a file or directory."""
        headers = dict(self._headers)
        headers["X-Destination"] = dest_path
        resp = requests.request(
            "MOVE", urljoin(self._url, src_path), headers=headers, **self._kwargs
        )
        resp.raise_for_status()
        return resp

    @request_exception_wrapper
    def delete(self, path):
        """Send a DELETE request to the device to delete a file or directory."""
        resp = requests.delete(
            urljoin(self._url, path), headers=self._headers, **self._kwargs
        )
        resp.raise_for_status()
        return resp

    def cp_devices(self):
        """Get device information."""
        return self.get("cp/devices.json")

    def cp_version(self):
        """Get CircuitPython version information."""
        return self.get("cp/version.json")

    def cp_diskinfo(self):
        """Get disk usage information."""
        return self.get("cp/diskinfo.json")

    def code_web(self):
        """Open the web code editor in a browser."""
        webbrowser.open(urljoin(self._url, "code/"))

    def files_web(self):
        """Open the file browser in a browser."""
        webbrowser.open(urljoin(self._url, "fs/"))

    def repl_web(self):
        """Open the web REPL in a browser."""
        url = urljoin(self._url, "cp/serial/")
        webbrowser.open(url)

    def open_repl_ws(self):
        """Connect to the device's REPL via WebSocket."""
        ws_url = self._url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = urljoin(ws_url, "cp/serial/")

        import base64

        auth_header = "Basic " + base64.b64encode(
            f":{self._auth[1]}".encode("utf-8")
        ).decode("utf-8")

        headers = {"Authorization": auth_header}

        def on_message(ws, message):
            print(message, end="")

        def on_error(ws, error):
            print("Error:", error)

        def on_close(ws, close_status_code, close_msg):
            print("WebSocket closed")

        def on_open(ws):
            print("WebSocket connection opened")

        self._ws = websocket.WebSocketApp(
            ws_url,
            header=headers,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )

        self._ws_thread = threading.Thread(target=self._ws.run_forever, daemon=True)
        self._ws_thread.start()

        return self._ws

    def close_repl_ws(self):
        """
        Closes the WebSocket connection and cleans up resources.
        """
        if self._ws:
            self._ws.close()

        if self._ws_thread and self._ws_thread.is_alive():
            ...

    def run_repl_ws(self):
        """Starts an interactive REPL session."""
        try:
            self.open_repl_ws()
            print("Connecting to REPL. Press Ctrl+C or Ctrl+D to exit.")

            # The input thread will handle getting user input and sending it
            def input_thread_func():
                while True:
                    try:
                        # Add a carriage return for the REPL to process the command
                        text = input(">>> ")
                        self._ws.send(text + "\r")
                    except (IOError, websocket.WebSocketConnectionClosedException):
                        print("Connection closed by server.")
                        break
                    except EOFError:
                        print("Exiting...")
                        break

            input_thread = threading.Thread(target=input_thread_func, daemon=True)
            input_thread.start()

            # The main thread now simply waits for the WebSocket thread to close
            # This allows the WebSocket thread to print output without being blocked
            while self._ws_thread.is_alive() and input_thread.is_alive():
                self._ws_thread.join(timeout=1)

        except KeyboardInterrupt:
            print("Interrupted, closing connection...")
        finally:
            self.close_repl_ws()
            if self._ws_thread and self._ws_thread.is_alive():
                self._ws_thread.join()


def ptree(tree_dict, prefix="", path_root=None):
    """
    Prints a formatted tree from a dictionary representation of a file system.
    """
    if path_root:
        print(Path(path_root).as_posix() + "/")

    items = list(tree_dict.items())
    for i, (path, content) in enumerate(items):
        is_last = i == len(items) - 1
        name = Path(path).name

        new_prefix_item = "└── " if is_last else "├── "
        new_next_prefix = prefix + ("    " if is_last else "│   ")

        if isinstance(content, dict):
            print(f"{prefix}{new_prefix_item}{name}/")
            ptree(content, prefix=new_next_prefix)
        elif isinstance(content, str) and content.startswith("Error"):
            print(f"{prefix}{new_prefix_item}{RED}{name} ({content}){RESET}")
        else:
            display_name = f"{prefix}{new_prefix_item}{name}"

            if name.endswith(".py"):
                print(f"{GREEN}{display_name}{RESET}")
            elif name.endswith(".mpy"):
                print(f"{YELLOW}{display_name}{RESET}")
            else:
                print(f"{RED}{display_name}{RESET}")


class Device:
    """
    Represents a CircuitPython device and its local cache.
    """

    def __init__(self, client: Client, local_path: os.PathLike = DEFAULT_CACHE_PATH):
        self.client = client
        self._version = self.client.cp_version().json()
        if not self.uid:
            raise UnknownCircuitPythonDevice("Unknown CircuitPython UID")
        self._cache_path: Path = Path(local_path) / self.uid
        self._init_cache()

    @property
    def uid(self):
        return self._version.get("UID", None)

    @property
    def version(self):
        return self._version

    @property
    def disk_info(self):
        return self.client.cp_diskinfo().json()

    @property
    def list_backups(self):
        return list((self._cache_path / "_bak").iterdir())

    @property
    def cache_path(self):
        return self._cache_path

    def _init_cache(self):
        """Initializes the local cache directory."""
        os.makedirs(self._cache_path, exist_ok=True)
        with open(self._cache_path / "version.py", "w") as fp:
            json.dump(self._version, fp)

    @staticmethod
    def auto_backup(cache_path: os.PathLike = DEFAULT_CACHE_PATH):
        """Creates an automatic backup of the local file system cache."""
        cache_path = Path(cache_path)
        fs_dir = cache_path / "fs"
        bak_dir = cache_path / "_bak"
        try:
            if fs_dir.exists() and fs_dir.is_dir():
                print("Backup of CircuitPython device...", end="")
                dt = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                backup_path = bak_dir / dt
                os.makedirs(backup_path, exist_ok=True)
                shutil.copytree(fs_dir, backup_path, dirs_exist_ok=True)
                print(f"Created backup at: {backup_path}")
                return backup_path
        except OSError:
            print(f"Failed to create backup of CircuitPython device.")
        return None

    @staticmethod
    def restore_backup(cache_path: os.PathLike, backup_path: os.PathLike):
        """Restores the local file system cache from a backup."""
        cache_path = Path(cache_path)
        backup_path = Path(backup_path)
        fs_dir = cache_path / "fs"
        try:
            if backup_path.exists() and backup_path.is_dir():
                os.makedirs(fs_dir, exist_ok=True)
                shutil.copytree(backup_path, fs_dir, dirs_exist_ok=True)
            else:
                raise FileNotFoundError("Backup not found or corrupted")
        except OSError:
            print(f"Failed to restore backup of CircuitPython device.")

    def tree(self, path: os.PathLike = "fs/", tree_=None):
        """Recursively builds a dictionary representation of the device's file system."""
        path = Path(path)
        if tree_ is None:
            tree_ = {}

        current_tree = {}
        tree_[path.as_posix()] = current_tree

        try:
            resp = self.client.get(path.as_posix() + "/")
            j = resp.json()
        except Exception as e:
            tree_[path.as_posix()] = f"Error: {e}"
            return tree_

        files = j.get("files", [])
        for f in files:
            name = f.get("name")
            is_dir = f.get("directory")
            p = path / name
            if is_dir:
                self.tree(p, current_tree)
            else:
                current_tree[p.as_posix()] = None

        return tree_

    def glob(
        self, pattern: str = None, *, root_path: os.PathLike = "fs/"
    ) -> Iterator[str]:
        """
        Recursively collects and yields file and directory paths from the device.

        :param pattern: A Unix-style glob pattern to match filenames (e.g., "*.py").
        :param root_path: The starting path to glob from.
        :yields: The path of a file or directory as a string.
        """
        root_path = Path(root_path)

        # Handle the root path itself
        if not pattern:
            yield root_path.as_posix() + "/"

        def _recursive_glob(path: Path, pattern_: str = "*"):
            """A helper function to recursively find and yield paths matching a pattern."""
            try:
                resp = self.client.get(path.as_posix() + "/")
                j = resp.json()
            except ClientRequestError:
                return

            files = j.get("files", [])
            for f in files:
                name = f.get("name")
                is_dir = f.get("directory")
                p = path / name

                # Check if the path should be yielded
                if not pattern_ or fnmatch(p.name, pattern_):
                    if is_dir:
                        yield p.as_posix() + "/"
                    else:
                        yield p.as_posix()

                # Recurse into subdirectories
                if is_dir:
                    # The yield from statement delegates to the inner generator
                    yield from _recursive_glob(p, pattern_)

        yield from _recursive_glob(root_path, pattern)

    def pull(self):
        """
        Pulls files from the device to the local cache.
        """
        backup_path = self.auto_backup(self._cache_path)
        try:
            for path in self.glob():
                print(f"Attempting to pull: {path} ... ", end="")
                if path.endswith("/"):
                    self.client.get(path)
                    os.makedirs(self._cache_path / path, exist_ok=True)
                    print("Directory created.")
                else:
                    self.download(path, self._cache_path / path)
                    print("File downloaded.")
            print("Pull done")
        except Exception as e:
            print(f"Error: {e}")
            print("Aborting...")
            if backup_path:
                self.restore_backup(self._cache_path, backup_path)

    def push(self):
        """
        Pushes files from the local cache to the device.
        """
        fs = self._cache_path / "fs"
        if fs.exists() and fs.is_dir():
            try:
                for path in fs.rglob("*"):
                    rel_path = path.relative_to(self._cache_path)
                    print(f"Attempting to push: {rel_path} ... ", end="")
                    if path.is_dir():
                        self.client.put(rel_path.as_posix() + "/")
                        print("Directory created.")
                    else:
                        self.upload(rel_path.as_posix(), path)
                        print("File uploaded.")
                print("Push done")
            except ClientRequestError as e:
                print(f"Error: {e}")

    def upload(self, path, filename):
        """Upload a local file to the device."""
        with open(filename, "rb") as fp:
            return self.client.put(path, data=fp)

    def download(self, path, dest_filename):
        """Download a file from the server to local disk."""
        response = self.client.get(path)
        with open(dest_filename, "wb") as fp:
            fp.write(response.content)
        return dest_filename
