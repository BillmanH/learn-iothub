"""
ptz_service.py — ONVIF PTZ service blueprint.
"""
from __future__ import annotations

import logging
import threading

from flask import Blueprint, current_app, request
from lxml import etree

from soap_utils import (
    NS, Timer, action_not_supported, soap_action_from_request,
    soap_fault, soap_response,
)

logger = logging.getLogger("onvifsim.ptz_service")

ptz_bp = Blueprint("ptz_service", __name__)

_NODE_TOKEN    = "ptz_node_0"
_CONFIG_TOKEN  = "ptz_config_0"

# In-memory PTZ position, shared across requests
_pos_lock = threading.Lock()
_position = {"pan": 0.0, "tilt": 0.0, "zoom": 0.0}


def _get_pos():
    with _pos_lock:
        return dict(_position)


def _set_pos(pan=None, tilt=None, zoom=None):
    with _pos_lock:
        if pan  is not None: _position["pan"]  = float(pan)
        if tilt is not None: _position["tilt"] = float(tilt)
        if zoom is not None: _position["zoom"] = float(zoom)


@ptz_bp.route("/onvif/ptz_service", methods=["POST"])
def handle():
    t = Timer()
    client_ip = request.remote_addr or "-"
    action = soap_action_from_request(request)

    try:
        result = _dispatch(action, client_ip)
    except Exception as exc:
        logger.error("%-32s %s  -> 500  %s", action, client_ip, exc, exc_info=True)
        return soap_fault(reason=str(exc))

    elapsed = t.ms()
    pos = _get_pos()
    if action in ("AbsoluteMove", "RelativeMove", "ContinuousMove", "GotoHomePosition", "GotoPreset"):
        logger.info(
            "%-32s %s  -> 200 OK pan=%.2f tilt=%.2f zoom=%.2f (%.1fms)",
            action, client_ip, pos["pan"], pos["tilt"], pos["zoom"], elapsed,
        )
    elif action == "GetStatus":
        logger.info(
            "%-32s %s  -> 200 OK pan=%.2f tilt=%.2f zoom=%.2f (%.1fms)",
            action, client_ip, pos["pan"], pos["tilt"], pos["zoom"], elapsed,
        )
    else:
        logger.info("%-32s %s  -> 200 OK (%.1fms)", action, client_ip, elapsed)
    return result


def _dispatch(action: str, client_ip: str):
    handlers = {
        # Tier 1
        "GetNodes":          _get_nodes,
        "GetStatus":         _get_status,
        "GetPresets":        _get_presets,
        "GotoHomePosition":  _goto_home_position,
        # Tier 2
        "AbsoluteMove":      _absolute_move,
        "RelativeMove":      _relative_move,
        "ContinuousMove":    _stub_empty,
        "Stop":              _stub_empty,
        "GetConfigurations": _get_configurations,
        "GetConfiguration":  _get_configurations,
        "GetConfigurationOptions": _stub_empty,
        "SetPreset":         _stub_empty,
        "RemovePreset":      _stub_empty,
        "GotoPreset":        _goto_preset,
        "SetHomePosition":   _stub_empty,
    }
    handler = handlers.get(action)
    if handler is None:
        logger.warning("%-32s %s  -> 400 ActionNotSupported", action, client_ip)
        return action_not_supported(action)
    return handler()


# ---------------------------------------------------------------------------
# Tier 1
# ---------------------------------------------------------------------------

def _get_nodes():
    tptz = NS["tptz"]
    tt   = NS["tt"]

    resp = etree.Element(f"{{{tptz}}}GetNodesResponse")
    node = etree.SubElement(resp, f"{{{tptz}}}PTZNode", attrib={"token": _NODE_TOKEN})
    etree.SubElement(node, f"{{{tt}}}Name").text = "PTZNode"
    spaces = etree.SubElement(node, f"{{{tt}}}SupportedPTZSpaces")
    for space_tag, uri in (
        ("AbsolutePanTiltPositionSpace",    "http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace"),
        ("AbsoluteZoomPositionSpace",       "http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace"),
        ("RelativePanTiltTranslationSpace", "http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationGenericSpace"),
        ("RelativeZoomTranslationSpace",    "http://www.onvif.org/ver10/tptz/ZoomSpaces/TranslationGenericSpace"),
        ("ContinuousPanTiltVelocitySpace",  "http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace"),
        ("ContinuousZoomVelocitySpace",     "http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace"),
    ):
        space = etree.SubElement(spaces, f"{{{tt}}}{space_tag}")
        etree.SubElement(space, f"{{{tt}}}URI").text = uri
        r = etree.SubElement(space, f"{{{tt}}}XRange")
        etree.SubElement(r, f"{{{tt}}}Min").text = "-1.0"
        etree.SubElement(r, f"{{{tt}}}Max").text = "1.0"
    etree.SubElement(node, f"{{{tt}}}MaximumNumberOfPresets").text = "10"
    etree.SubElement(node, f"{{{tt}}}HomeSupported").text          = "true"
    return soap_response(resp)


def _get_status():
    tptz = NS["tptz"]
    tt   = NS["tt"]
    pos  = _get_pos()

    resp   = etree.Element(f"{{{tptz}}}GetStatusResponse")
    status = etree.SubElement(resp, f"{{{tptz}}}PTZStatus")
    position = etree.SubElement(status, f"{{{tt}}}Position")
    pt = etree.SubElement(position, f"{{{tt}}}PanTilt",
                          attrib={"x": str(pos["pan"]), "y": str(pos["tilt"]),
                                  "space": "http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace"})
    z  = etree.SubElement(position, f"{{{tt}}}Zoom",
                          attrib={"x": str(pos["zoom"]),
                                  "space": "http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace"})
    move_status = etree.SubElement(status, f"{{{tt}}}MoveStatus")
    etree.SubElement(move_status, f"{{{tt}}}PanTilt").text = "Idle"
    etree.SubElement(move_status, f"{{{tt}}}Zoom").text    = "Idle"
    return soap_response(resp)


def _get_presets():
    from flask import current_app
    cfg     = current_app.config["CFG"]
    presets = cfg.get("ptz", {}).get("presets", [])
    tptz    = NS["tptz"]
    tt      = NS["tt"]

    resp = etree.Element(f"{{{tptz}}}GetPresetsResponse")
    for p in presets:
        preset = etree.SubElement(resp, f"{{{tptz}}}Preset",
                                  attrib={"token": p["token"]})
        etree.SubElement(preset, f"{{{tt}}}Name").text = p["name"]
        position = etree.SubElement(preset, f"{{{tt}}}PTZPosition")
        etree.SubElement(position, f"{{{tt}}}PanTilt",
                         attrib={"x": str(p["pan"]), "y": str(p["tilt"]),
                                 "space": "http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace"})
        etree.SubElement(position, f"{{{tt}}}Zoom",
                         attrib={"x": str(p["zoom"]),
                                 "space": "http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace"})
    return soap_response(resp)


def _goto_home_position():
    from flask import current_app
    cfg  = current_app.config["CFG"]
    home = cfg.get("ptz", {}).get("home_position", {"pan": 0.0, "tilt": 0.0, "zoom": 0.0})
    _set_pos(pan=home["pan"], tilt=home["tilt"], zoom=home["zoom"])
    tptz = NS["tptz"]
    resp = etree.Element(f"{{{tptz}}}GotoHomePositionResponse")
    return soap_response(resp)


# ---------------------------------------------------------------------------
# Tier 2
# ---------------------------------------------------------------------------

def _absolute_move():
    try:
        from soap_utils import parse_body
        body = parse_body(request.data)
        tt   = NS["tt"]
        pt   = body.find(f".//{{{tt}}}PanTilt")
        z    = body.find(f".//{{{tt}}}Zoom")
        if pt is not None:
            _set_pos(pan=pt.get("x", 0), tilt=pt.get("y", 0))
        if z is not None:
            _set_pos(zoom=z.get("x", 0))
    except Exception:
        pass
    tptz = NS["tptz"]
    return soap_response(etree.Element(f"{{{tptz}}}AbsoluteMoveResponse"))


def _relative_move():
    try:
        from soap_utils import parse_body
        body = parse_body(request.data)
        tt   = NS["tt"]
        pt   = body.find(f".//{{{tt}}}PanTilt")
        z    = body.find(f".//{{{tt}}}Zoom")
        pos  = _get_pos()
        if pt is not None:
            _set_pos(
                pan=pos["pan"]   + float(pt.get("x", 0)),
                tilt=pos["tilt"] + float(pt.get("y", 0)),
            )
        if z is not None:
            _set_pos(zoom=pos["zoom"] + float(z.get("x", 0)))
    except Exception:
        pass
    tptz = NS["tptz"]
    return soap_response(etree.Element(f"{{{tptz}}}RelativeMoveResponse"))


def _goto_preset():
    try:
        from flask import current_app
        from soap_utils import parse_body
        body  = parse_body(request.data)
        tptz2 = NS["tptz"]
        token_el = body.find(f"{{{tptz2}}}PresetToken")
        if token_el is not None:
            token = token_el.text
            presets = current_app.config["CFG"].get("ptz", {}).get("presets", [])
            for p in presets:
                if p["token"] == token:
                    _set_pos(pan=p["pan"], tilt=p["tilt"], zoom=p["zoom"])
                    break
    except Exception:
        pass
    tptz = NS["tptz"]
    return soap_response(etree.Element(f"{{{tptz}}}GotoPresetResponse"))


def _get_configurations():
    tptz = NS["tptz"]
    tt   = NS["tt"]
    pos  = _get_pos()

    resp = etree.Element(f"{{{tptz}}}GetConfigurationsResponse")
    cfg_el = etree.SubElement(resp, f"{{{tptz}}}PTZConfiguration",
                              attrib={"token": _CONFIG_TOKEN})
    etree.SubElement(cfg_el, f"{{{tt}}}Name").text    = "PTZConfig"
    etree.SubElement(cfg_el, f"{{{tt}}}UseCount").text = "1"
    etree.SubElement(cfg_el, f"{{{tt}}}NodeToken").text = _NODE_TOKEN
    return soap_response(resp)


def _stub_empty():
    action = soap_action_from_request(request)
    tptz = NS["tptz"]
    resp = etree.Element(f"{{{tptz}}}{action}Response")
    return soap_response(resp)
