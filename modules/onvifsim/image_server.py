"""
image_server.py — looping image server for onvifsim.

Maintains a background thread that advances through a sorted list of image
files on a configurable interval. Exposes three HTTP endpoints via a Flask
Blueprint:

  GET /snapshot  — current image as image/jpeg
  GET /stream    — MJPEG multipart stream
  GET /health    — JSON status
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from flask import Blueprint, Response, current_app

logger = logging.getLogger("onvifsim.image_server")

image_bp = Blueprint("image_server", __name__)

# ---------------------------------------------------------------------------
# ImageLoop — runs in background thread
# ---------------------------------------------------------------------------

_SUPPORTED = {".jpg", ".jpeg", ".png", ".bmp"}


class ImageLoop:
    def __init__(self, images_dir: str, interval_ms: int, extensions: list[str] | None = None):
        self._dir      = Path(images_dir)
        self._interval = interval_ms / 1000.0
        self._exts     = {e.lower() for e in (extensions or list(_SUPPORTED))}
        self._lock     = threading.Lock()
        self._files: list[Path] = []
        self._index    = 0
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()

    def start(self):
        self._load_files()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="image-loop")
        self._thread.start()
        if self._files:
            logger.info(
                "Image loop started: interval=%dms, first=%s",
                int(self._interval * 1000),
                self._files[0].name,
            )
        else:
            logger.warning("Image loop started but no images found in '%s'", self._dir)

    def stop(self):
        self._stop_evt.set()

    def _load_files(self):
        if not self._dir.is_dir():
            logger.error("Images dir '%s' is empty or unreadable — snapshot will return 503", self._dir)
            return
        files = sorted(
            p for p in self._dir.iterdir()
            if p.is_file() and p.suffix.lower() in self._exts
        )
        with self._lock:
            self._files = files
            self._index = 0
        logger.info("Images dir: %s (%d files found)", self._dir, len(files))

    def _loop(self):
        while not self._stop_evt.wait(self._interval):
            with self._lock:
                if not self._files:
                    continue
                self._index = (self._index + 1) % len(self._files)
                name = self._files[self._index].name
                count = self._index + 1
                total = len(self._files)
            if self._index == 0:
                logger.debug("Loop tick: wrapped around to %s", name)
            else:
                logger.debug("Loop tick: advanced to %s (%d/%d)", name, count, total)

    def current_file(self) -> Path | None:
        with self._lock:
            if not self._files:
                return None
            return self._files[self._index]

    def current_name(self) -> str:
        f = self.current_file()
        return f.name if f else "(none)"

    def all_files(self) -> list[Path]:
        with self._lock:
            return list(self._files)


# Module-level singleton — set by app.py before registering blueprint
_loop: ImageLoop | None = None


def set_loop(loop: ImageLoop):
    global _loop
    _loop = loop


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@image_bp.route("/health")
def health():
    loop: ImageLoop = current_app.config["IMAGE_LOOP"]
    auth_mode: str  = current_app.config["AUTH_MODE"]
    return Response(
        json.dumps({
            "status": "ok",
            "current_image": loop.current_name(),
            "auth_mode": auth_mode,
        }),
        content_type="application/json",
    )


@image_bp.route("/snapshot")
def snapshot():
    loop: ImageLoop = current_app.config["IMAGE_LOOP"]
    t0 = time.perf_counter()
    client_ip = _client_ip()

    current = loop.current_file()
    if current is None:
        logger.warning("GET /snapshot -> 503 (no images available)  %s", client_ip)
        return Response("No images available", status=503)

    try:
        data = current.read_bytes()
        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(
            "GET /snapshot -> 200 OK  current=%s  (%.1fms)  %s",
            current.name, elapsed, client_ip,
        )
        return Response(data, content_type="image/jpeg")
    except OSError as exc:
        logger.error("GET /snapshot -> 500  %s  %s", exc, client_ip)
        return Response("Failed to read image", status=500)


@image_bp.route("/stream")
def stream():
    loop: ImageLoop = current_app.config["IMAGE_LOOP"]
    client_ip = _client_ip()
    logger.info("GET /stream -> client connected (MJPEG)  %s", client_ip)

    frame_delay = current_app.config.get("STREAM_FRAME_DELAY", 0.1)

    def generate():
        frames_sent = 0
        try:
            while True:
                current = loop.current_file()
                if current is None:
                    time.sleep(0.5)
                    continue
                try:
                    data = current.read_bytes()
                except OSError:
                    time.sleep(0.5)
                    continue
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + data
                    + b"\r\n"
                )
                frames_sent += 1
                time.sleep(frame_delay)
        except GeneratorExit:
            logger.info(
                "GET /stream -> client disconnected after %d frames  %s",
                frames_sent, client_ip,
            )

    return Response(
        generate(),
        content_type="multipart/x-mixed-replace; boundary=frame",
    )


def _client_ip() -> str:
    from flask import request
    return request.remote_addr or "-"
