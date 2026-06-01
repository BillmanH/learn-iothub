"""
app.py — onvifsim Flask entrypoint.

Loads config.yaml (with env var overrides), starts the image loop,
registers all SOAP service blueprints with auth middleware, and
starts the Flask dev server.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path

import yaml
from flask import Flask

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    # Environment variable overrides
    _env_override(cfg, "images",  "dir",              "IMAGES_DIR")
    _env_override(cfg, "server",  "port",             "ONVIF_PORT", cast=int)
    _env_override(cfg, "auth",    "mode",             "ONVIF_AUTH_MODE")
    _env_override(cfg, "auth",    "username",         "ONVIF_AUTH_USERNAME")
    _env_override(cfg, "auth",    "password",         "ONVIF_AUTH_PASSWORD")
    _env_override(cfg, "device",  "name",             "ONVIF_DEVICE_NAME")
    _env_override(cfg, "device",  "serial",           "ONVIF_DEVICE_SERIAL")
    _env_override(cfg, "logging", "level",            "ONVIF_LOG_LEVEL")
    _env_override(cfg, "logging", "show_soap_bodies", "ONVIF_SHOW_SOAP_BODIES",
                  cast=lambda v: v.lower() in ("1", "true", "yes"))
    return cfg


def _env_override(cfg: dict, section: str, key: str, env_var: str, cast=None):
    val = os.environ.get(env_var)
    if val is not None:
        cfg.setdefault(section, {})
        cfg[section][key] = cast(val) if cast else val


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(cfg: dict):
    level_name = cfg.get("logging", {}).get("level", "info").upper()
    show_soap  = cfg.get("logging", {}).get("show_soap_bodies", False)

    level = getattr(logging, level_name, logging.INFO)
    if show_soap:
        level = logging.DEBUG

    fmt = "[ONVIFSIM] %(asctime)s.%(msecs)03d | %(levelname)-5s | %(name)-16s | %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    # Quiet Flask/Werkzeug noise unless debug
    if level > logging.DEBUG:
        logging.getLogger("werkzeug").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(cfg: dict | None = None) -> Flask:
    if cfg is None:
        cfg = _load_config()

    _setup_logging(cfg)
    log = logging.getLogger("onvifsim.app")

    from image_server import ImageLoop, image_bp
    from auth import make_auth_hook
    from device_service  import device_bp
    from media_service   import media_bp
    from ptz_service     import ptz_bp
    from imaging_service import imaging_bp
    from events_service  import events_bp

    # Start image loop
    images_cfg = cfg.get("images", {})
    loop = ImageLoop(
        images_dir   = images_cfg.get("dir", "/images"),
        interval_ms  = images_cfg.get("loop_interval_ms", 2000),
        extensions   = images_cfg.get("supported_extensions"),
    )
    loop.start()

    app = Flask(__name__)
    app.config["CFG"]              = cfg
    app.config["IMAGE_LOOP"]       = loop
    app.config["AUTH_MODE"]        = cfg.get("auth", {}).get("mode", "none")
    app.config["STREAM_FRAME_DELAY"] = images_cfg.get("loop_interval_ms", 2000) / 1000.0

    # Auth hook applied to all SOAP blueprints
    auth_hook = make_auth_hook(cfg)

    for bp in (device_bp, media_bp, ptz_bp, imaging_bp, events_bp):
        bp.before_request(auth_hook)
        app.register_blueprint(bp)

    # Image / health endpoints (no auth)
    app.register_blueprint(image_bp)

    port    = cfg["server"]["port"]
    name    = cfg["device"]["name"]
    version = "1.0.0"
    auth_mode = cfg["auth"]["mode"]
    auth_user = cfg["auth"]["username"]

    log.info("Starting onvifsim v%s on port %d", version, port)
    log.info("Auth mode: %s (username: %s)", auth_mode, auth_user)

    return app, loop, port


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    cfg   = _load_config()
    app, loop, port = create_app(cfg)
    log = logging.getLogger("onvifsim.app")

    def _shutdown(signum, frame):
        log.info("Shutting down (signal %d)", signum)
        loop.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
