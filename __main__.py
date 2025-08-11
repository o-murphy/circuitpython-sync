import argparse
import os
import shutil
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
import websocket

DEFAULT_URL = "http://circuitpython.local/"
DEFAULT_PASS = "passw0rd"
DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json"
}
DEFAULT_KWARGS = {
    "allow_redirects": True,
    "timeout": 5
}

# ANSI color codes
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


class Client:
    def __init__(self, url=DEFAULT_URL, password=DEFAULT_PASS, headers=None, **kwargs):
        if not url.endswith("/"):
            url += "/"
        self._url = url
        self._headers = headers or DEFAULT_HEADERS
        self._auth = ("", password)
        self._kwargs = {
            "auth": self._auth,
        }
        self._kwargs.update(kwargs)

        self._ws = None
        self._ws_thread = None
        self._ws_buffer = ""
        self._ws_buffer_lock = threading.Lock()
        self._ws_buffer_timer = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def close(self):
        # Закриваємо WebSocket
        if self._ws:
            self._ws.close()

        # Зупиняємо таймер
        if self._ws_buffer_timer and self._ws_buffer_timer.is_alive():
            self._ws_buffer_timer.cancel()

        # Чекаємо завершення потоку з таймаутом
        if self._ws_thread and self._ws_thread.is_alive():
            # self._ws_thread.join(timeout=2)
            ...

        # Очищаємо буфер
        with self._ws_buffer_lock:
            self._ws_buffer = ""

    def options(self):
        return requests.options(
            self._url,
            **self._kwargs
        )

    def get(self, path):
        return requests.get(
            urljoin(self._url, path),
            headers=self._headers,
            **self._kwargs
        )

    def put(self, path, data=None):
        return requests.put(
            urljoin(self._url, path),
            data=data,
            headers=self._headers,
            **self._kwargs
        )

    def move(self, src_path, dest_path):
        headers = dict(self._headers)
        headers["X-Destination"] = dest_path
        return requests.request(
            "MOVE",
            urljoin(self._url, src_path),
            headers=headers,
            **self._kwargs
        )

    def delete(self, path):
        return requests.delete(
            urljoin(self._url, path),
            headers=self._headers,
            **self._kwargs
        )

    def upload(self, path, filename):
        with open(filename, "rb") as f:
            return requests.put(
                urljoin(self._url, path),
                data=f,
                headers=self._headers,
                **self._kwargs
            )

    def download(self, path, dest_filename):
        """Download a file from the server to local disk."""
        response = requests.get(
            urljoin(self._url, path),
            **self._kwargs
        )
        response.raise_for_status()
        with open(dest_filename, "wb") as f:
            f.write(response.content)
        return dest_filename

    def cp_dev(self):
        return self.get("cp/devices.json")

    def cp_ver(self):
        return self.get("cp/version.json")

    def cp_disk(self):
        return self.get("cp/diskinfo.json")

    def code(self):
        webbrowser.open(urljoin(self._url, "code/"))

    def files(self):
        webbrowser.open(urljoin(self._url, "fs/"))

    def repl_web(self):
        # This opens the serial terminal UI in the default browser (authenticated via URL)
        # Since it's basic auth, embed credentials in URL or rely on browser prompt.
        url = urljoin(self._url, "cp/serial/")
        # If you want to embed credentials in URL (be careful about security!)
        # url = url.replace("://", f"://:{self._auth[1]}@")
        webbrowser.open(url)

    def _process_ws_buffer(self):
        with self._ws_buffer_lock:
            if self._ws_buffer:
                print("Received chunk:", self._ws_buffer)
                self._ws_buffer = ""

    def repl_ws(self):
        ws_url = self._url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = urljoin(ws_url, "cp/serial/")

        import base64
        auth_header = "Basic " + base64.b64encode(
            f":{self._auth[1]}".encode("utf-8")
        ).decode("utf-8")

        headers = {"Authorization": auth_header}

        def on_message(ws, message):
            with self._ws_buffer_lock:
                self._ws_buffer += message
            if self._ws_buffer_timer and self._ws_buffer_timer.is_alive():
                self._ws_buffer_timer.cancel()
            self._ws_buffer_timer = threading.Timer(0.1, self._process_ws_buffer)
            self._ws_buffer_timer.start()

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

    def run(self):
        try:
            self.repl_ws()
            while True:
                text = input("> ")
                self._ws.send(text)
        except KeyboardInterrupt:
            self.close()
            print("Interrupted, closing connection...")


class Device:
    def __init__(self, client: Client, local_path: os.PathLike = "CircuitPython"):
        self.client = client
        self.ver = self.client.cp_ver().json()
        self.uid = self.ver.get('uid')
        self.dev = Path(local_path) / self.uid

    def ptree(self, tree_dict, prefix='', path_root=None):

        if path_root:
            print(Path(path_root).as_posix() + "/")

        items = list(tree_dict.items())
        for i, (path, content) in enumerate(items):
            is_last = (i == len(items) - 1)
            name = Path(path).name

            new_prefix_item = "└── " if is_last else "├── "
            new_next_prefix = prefix + ("    " if is_last else "│   ")

            if isinstance(content, dict):
                print(f"{prefix}{new_prefix_item}{name}/")
                self.ptree(content, prefix=new_next_prefix)
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

    def _make_backup(self):
        if self.dev.exists():
            dt = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            bak = self.dev / "__bak" / dt
            os.makedirs(bak, exist_ok=True)

            for each in self.dev.iterdir():
                if not each.name == "__bak":
                    shutil.move(each, bak)

    def _fetch_local_cp(self):
        os.makedirs(self.dev / "cp", exist_ok=True)
        os.makedirs(self.dev / "fs", exist_ok=True)

        for f in {"version.json", "devices.json", "diskinfo.json"}:
            src = f"cp/{f}"
            dst = self.dev / "cp" / f
            self.client.download(src, dst)

    def _fetch_local_fs(self, tree_):
        for k, v in tree_.items():
            dst = self.dev / (k[1:] if k.startswith("/") else k)
            if isinstance(v, dict):
                os.makedirs(dst, exist_ok=True)
                self._fetch_local_fs(v)
            elif isinstance(v, str):
                if not v.startswith("Error"):
                    self.client.download(k, dst)
            elif v is None:
                self.client.download(k, dst)
            else:
                ...

    def tree(self, path: os.PathLike = "fs/", tree_=None):
        path = Path(path)
        if tree_ is None:
            tree_ = {}

        current_tree = {}
        tree_[path.as_posix()] = current_tree

        try:
            resp = self.client.get(path.as_posix() + "/")
            resp.raise_for_status()
            j = resp.json()
        except Exception as e:
            tree_[path.as_posix()] = f"Error: {e}"
            return tree_

        files = j.get('files', [])
        for f in files:
            name = f.get('name')
            is_dir = f.get("directory")
            p = path / name
            if is_dir:
                self.tree(p, current_tree)
            else:
                current_tree[p.as_posix()] = None

        return tree_

    def fetch(self):
        if self.uid:
            self._make_backup()
            self._fetch_local_cp()
            fs_tree = self.tree()
            self._fetch_local_fs(tree_=fs_tree)

    def sync(self):
        ...


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-u", "--url", type=str, help="device web workflow url", default=DEFAULT_URL)
    ap.add_argument("-p", "--pass", type=str, help="device web worflow password", default=DEFAULT_PASS)

    # ap.add_argument("--push", type=Path, nargs=2, help="push file(s) to remote device")
    # ap.add_argument("--pull", type=Path, nargs=2, help="push file(s) from remote device")
    # ap.add_argument("--repl", action="store_true")
    # ap.add_argument("--ver", action="store_true")
    # ap.add_argument("--dev", action="store_true")
    # ap.add_argument("--code", action="store_true")

    # ap.parse_args()


if __name__ == "__main__":
    import sys

    sys.argv = sys.argv[:]
    sys.argv += ["-h"]

    with Client(
            url="http://192.168.6.213/"
    ) as c:
        r = c.options()
        print("Status:", r.status_code)
        print("Headers:", r.headers)
        print("Body:", r.text)

        device = Device(c)
        device.fetch()
        device.ptree(device.tree())
