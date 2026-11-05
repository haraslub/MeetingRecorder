"""HTTP listener of the local API, on 127.0.0.1 only.

Requests arrive on worker threads of the HTTP server, but every command touches Qt objects,
so each one is handed to the Qt main thread and the worker waits for its result. A client
that times out gets 504; the command itself still runs once the main thread is free.
"""

import hmac
import json
import logging
import socket
import sys
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from PyQt6.QtCore import QObject, Qt, pyqtSignal

from audiorecorder.api import discovery

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
MAX_BODY_BYTES = 64 * 1024
COMMAND_TIMEOUT_SECONDS = 15


class ApiError(Exception):
    """A refused request: HTTP status, a stable machine-readable code and a message."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind the same port and receive requests
    # meant for this one; the exclusive flag below refuses that instead.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class ApiServer(QObject):
    """Runs the listener while the API is allowed, and keeps api.json in step with it."""

    _call = pyqtSignal(object)

    def __init__(self, handle, record_path=discovery.RECORD_PATH):
        """handle(method, path, query, body) returns a JSON-ready dict or raises ApiError."""
        super().__init__()
        self._handle = handle
        self._record_path = record_path
        self._server = None
        self._thread = None
        self._settings = None
        self.error = None
        self._call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @property
    def port(self):
        return self._server.server_address[1] if self._server is not None else None

    def status(self):
        if self._server is not None:
            return f"Listening on http://{HOST}:{self.port}"
        if self.error:
            return self.error
        return "Off"

    def configure(self, config):
        """Start, restart or stop the listener to match the settings."""
        enabled = bool(config.get("api_enabled")) and bool(config.get("api_token"))
        settings = (config["api_port"], config["api_token"]) if enabled else None
        if settings == self._settings and (self._server is not None or settings is None):
            return
        self._stop()
        switched_off = self._settings is not None and settings is None
        self._settings = settings
        self.error = None
        if switched_off:
            discovery.remove_record(self._record_path)
        if settings is None:
            return
        port, token = settings
        try:
            self._server = _Server((HOST, port), self._handler(token))
        except OSError as error:
            self.error = f"Port {port} is not available: {error.strerror or error}"
            log.warning("local API could not listen on port %s: %s", port, error)
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="local-api", daemon=True)
        self._thread.start()
        try:
            discovery.write_record(self.port, token, self._record_path)
        except OSError:
            log.exception("the local API record could not be written")

    def close(self):
        """Stop listening on quit. api.json stays, so a client can start the application."""
        self._stop()

    def _stop(self):
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
        self._server = None
        self._thread = None

    def _run(self, job):
        future, method, path, query, body = job
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(self._handle(method, path, query, body))
        except BaseException as error:
            future.set_exception(error)

    def _dispatch(self, method, path, query, body):
        future = Future()
        self._call.emit((future, method, path, query, body))
        return future.result(timeout=COMMAND_TIMEOUT_SECONDS)

    def _handler(self, token):
        dispatch = self._dispatch
        expected = f"Bearer {token}".encode()

        class Handler(BaseHTTPRequestHandler):
            server_version = "MeetingRecorder"

            def do_GET(self):
                self._respond("GET")

            def do_POST(self):
                self._respond("POST")

            def _respond(self, method):
                try:
                    status, payload = 200, {"ok": True, **self._serve(method)}
                except ApiError as error:
                    status = error.status
                    payload = {"ok": False, "code": error.code, "message": error.message}
                except FutureTimeout:
                    status, payload = 504, {
                        "ok": False, "code": "timeout",
                        "message": "The application did not answer in time."}
                except Exception as error:
                    log.exception("local API request failed")
                    status, payload = 500, {
                        "ok": False, "code": "internal_error", "message": str(error)}
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _serve(self, method):
                # A web page can reach 127.0.0.1 through a domain that resolves to it (DNS
                # rebinding); such a request carries that domain as its Host.
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
                if host not in ("127.0.0.1", "localhost"):
                    raise ApiError(403, "forbidden_host", "Only local clients may connect.")
                supplied = (self.headers.get("Authorization") or "").encode()
                if not hmac.compare_digest(supplied, expected):
                    raise ApiError(401, "unauthorized", "A valid bearer token is required.")
                url = urlsplit(self.path)
                query = {key: values[-1] for key, values in parse_qs(url.query).items()}
                return dispatch(method, url.path.rstrip("/"), query, self._body())

            def _body(self):
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    raise ApiError(400, "bad_request", "Invalid Content-Length.") from None
                if length > MAX_BODY_BYTES:
                    raise ApiError(413, "too_large", "The request body is too large.")
                if length <= 0:
                    return {}
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    raise ApiError(400, "bad_request", "The body is not valid JSON.") from None
                if not isinstance(body, dict):
                    raise ApiError(400, "bad_request", "The body must be a JSON object.")
                return body

            def log_message(self, format, *args):
                log.debug("local API: " + format, *args)

        return Handler
