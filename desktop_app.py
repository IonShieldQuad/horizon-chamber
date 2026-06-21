"""Horizon Chamber — Desktop Application Launcher.

Starts the FastAPI server in the background and opens a native web view
window using the system's built-in browser engine (Edge WebView2 on Windows,
WebKit on macOS). No browser tabs, no address bar — just the app.

Usage:
    python desktop_app.py              # launch window
    python desktop_app.py --port 9000  # custom port
    python desktop_app.py --debug      # verbose logs
"""

import argparse
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

# Load .env before anything else so that db.py / deepseek_client.py
# see the correct environment variables.
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Port helpers
# ---------------------------------------------------------------------------

DEFAULT_PORT = 8001


def _find_free_port(start: int = 8001, max_attempts: int = 50) -> int:
    """Return the first free port at or above *start*."""
    for port in range(start, start + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(
        f"No free port found in range {start}-{start + max_attempts - 1}"
    )


# ---------------------------------------------------------------------------
# Server wrapper — runs uvicorn in a background thread with clean shutdown
# ---------------------------------------------------------------------------


class ServerThread:
    """Manage the uvicorn server lifecycle in a background thread."""

    def __init__(self, port: int) -> None:
        self.port = port
        self._server = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Launch uvicorn in a daemon thread."""
        import uvicorn
        from uvicorn.config import Config
        from uvicorn.server import Server

        from main import app as fastapi_app

        config = Config(
            app=fastapi_app,
            host="127.0.0.1",
            port=self.port,
            log_level="info",
            reload=False,
            # Don't let uvicorn reconfigure logging — it crashes when
            # sys.stderr is None (PyInstaller --noconsole build).
            log_config=None,
        )
        self._server = Server(config=config)

        self._thread = threading.Thread(
            target=self._server.run,
            daemon=True,
            name="uvicorn-server",
        )
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        """Signal graceful shutdown (best-effort; daemon thread is killed
        automatically if the process exits)."""
        if self._server is not None:
            self._server.should_exit = True


# ---------------------------------------------------------------------------
# Wait for the HTTP server to become reachable
# ---------------------------------------------------------------------------


def _wait_for_server(url: str, *, timeout: float = 15.0) -> None:
    """Poll /api/health until the server responds 200 or *timeout* expires."""
    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{url}/api/health", timeout=2)
            if r.status_code == 200:
                logger.info("Server is ready")
                return
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        logger.debug("Waiting for server…")
        time.sleep(0.3)
    logger.error("Server did not start within %.1f s — aborting", timeout)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Window icon helper (Windows)
# ---------------------------------------------------------------------------


def _set_window_icon_async(title: str, icon_path: str, *, delay: float = 0.8) -> None:
    """Set the window icon after a short delay using the Win32 API.

    Launches a background daemon thread that waits for the GUI window to
    be created (the GUI event loop is running in the main thread), then
    loads the .ico file and sends WM_SETICON to the window handle.
    Silently does nothing on non-Windows platforms.
    """
    if sys.platform != "win32":
        return

    import ctypes

    def _set() -> None:
        time.sleep(delay)
        try:
            h_icon = ctypes.windll.user32.LoadImageW(
                None,
                icon_path,
                1,  # IMAGE_ICON
                0,  # width (0 = use actual size)
                0,  # height (0 = use actual size)
                0x00000010,  # LR_LOADFROMFILE
            )
            if not h_icon:
                logger.warning("Could not load icon from %s", icon_path)
                return

            hwnd = ctypes.windll.user32.FindWindowW(None, title)
            if not hwnd:
                logger.debug("Window not found (may not be ready yet)")
                return

            # WM_SETICON = 0x80, ICON_SMALL = 0, ICON_BIG = 1
            ctypes.windll.user32.SendMessageW(hwnd, 0x80, 0, h_icon)
            ctypes.windll.user32.SendMessageW(hwnd, 0x80, 1, h_icon)
            logger.debug("Window icon set successfully")
        except Exception as exc:
            logger.warning("Failed to set window icon: %s", exc)

    threading.Thread(target=_set, daemon=True, name="icon-setter").start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Horizon Chamber — Desktop App")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Port to serve on (default: auto, starting at {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Resolve port
    port = args.port if args.port else _find_free_port(DEFAULT_PORT)

    # ── Start the backend server ──────────────────────────────────────
    # Enable activity monitor in desktop mode by default
    if "AUTO_START_MONITOR" not in os.environ:
        os.environ["AUTO_START_MONITOR"] = "true"

    server = ServerThread(port)
    server.start()
    _wait_for_server(server.url)

    # ── HorizonApi JS bridge ──────────────────────────────────────────
    import monitor as _monitor

    class HorizonApi:
        """Python API exposed to JavaScript via PyWebView bridge."""

        def get_active_window(self) -> dict:
            """Return the current foreground window info."""
            return _monitor.get_current_activity() or {
                "app_name": "unknown",
                "window_title": "",
            }

        def get_idle_time(self) -> float:
            """Return idle seconds."""
            return _monitor.get_idle_seconds()

        def open_path(self, path: str) -> None:
            """Open a file or folder in the OS default handler."""
            import subprocess
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path])
            else:
                subprocess.run(["xdg-open", path])

        def set_wallpaper(self, image_path: str) -> None:
            """Change the desktop wallpaper (Windows only for now)."""
            import ctypes
            try:
                ctypes.windll.user32.SystemParametersInfoW(20, 0, image_path, 0)
            except Exception as exc:
                logger.warning("Failed to set wallpaper: %s", exc)

    # ── Launch native window ──────────────────────────────────────────
    import webview

    title = "Horizon Chamber"

    logger.info("Opening desktop window: %s", title)

    webview.create_window(
        title=title,
        url=server.url,
        width=1200,
        height=800,
        resizable=True,
        fullscreen=False,
        min_size=(900, 600),
        text_select=True,
        js_api=HorizonApi(),
    )

    # webview.start() blocks until the user closes the window.
    # Kick off the window-icon setter while the event loop runs.
    # Try the development path first, then the PyInstaller bundle path.
    dev_ico = Path(__file__).resolve().parent / "static" / "horizon_icon.ico"
    bundle_ico = Path(getattr(sys, "_MEIPASS", "")) / "static" / "horizon_icon.ico"
    ico_path = dev_ico if dev_ico.exists() else bundle_ico
    if ico_path.exists():
        _set_window_icon_async(title, str(ico_path))

    webview.start(private_mode=False)

    # ── Cleanup ───────────────────────────────────────────────────────
    logger.info("Window closed — shutting down server…")
    server.stop()
    logger.info("Goodbye.")


if __name__ == "__main__":
    main()
