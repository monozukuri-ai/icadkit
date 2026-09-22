"""Local viewer publication and a fixed-file, loopback-only HTTP server."""

from __future__ import annotations

import shutil
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def publish(staged: Path, output: Path) -> None:
    """Reserve a new directory; on failure remove only files created here."""
    output.mkdir()
    created = []
    try:
        for source in sorted(staged.iterdir()):
            target = output / source.name
            with target.open("xb") as stream:
                created.append(target)
                with source.open("rb") as content:
                    shutil.copyfileobj(content, stream)
    except BaseException:
        for target in created:
            target.unlink(missing_ok=True)
        try:
            output.rmdir()
        except OSError:
            pass
        raise


class ViewerServer:
    """Serve a snapshot of four viewer files on 127.0.0.1; no upload endpoints."""

    def __init__(self, directory: str | Path, *, port: int = 0) -> None:
        if isinstance(port, bool) or not isinstance(port, int):
            raise TypeError("port must be an integer")
        if not 0 <= port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        types = {
            "index.html": "text/html; charset=utf-8",
            "viewer.js": "text/javascript; charset=utf-8",
            "viewer.css": "text/css; charset=utf-8",
            "scene.json": "application/json; charset=utf-8",
        }
        payloads = {}
        for name, mime in types.items():
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError(f"Viewer file must not be a symlink: {name}")
            payloads["/" + name] = (path.read_bytes(), mime)
        payloads["/"] = payloads["/index.html"]

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._respond(body=True)

            def do_HEAD(self) -> None:
                self._respond(body=False)

            def _respond(self, *, body: bool) -> None:
                item = payloads.get(urlsplit(self.path).path)
                if item is None:
                    self.send_error(404)
                    return
                data, mime = item
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                    "base-uri 'none'",
                )
                self.end_headers()
                if body:
                    self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._thread: threading.Thread | None = None
        self._closed = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/"

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("Viewer server is closed")
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._server.serve_forever, daemon=True
            )
            self._thread.start()

    def open_browser(self) -> bool:
        return webbrowser.open(self.url)

    def close(self) -> None:
        if self._closed:
            return
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join()
        self._server.server_close()
        self._closed = True

    def __enter__(self) -> ViewerServer:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
