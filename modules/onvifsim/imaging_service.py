"""
imaging_service.py — ONVIF Imaging service blueprint.
"""
from __future__ import annotations

import logging

from flask import Blueprint, current_app, request
from lxml import etree

from soap_utils import (
    NS, Timer, action_not_supported, soap_action_from_request,
    soap_fault, soap_response,
)

logger = logging.getLogger("onvifsim.imaging_service")

imaging_bp = Blueprint("imaging_service", __name__)


@imaging_bp.route("/onvif/imaging_service", methods=["POST"])
def handle():
    t = Timer()
    client_ip = request.remote_addr or "-"
    action = soap_action_from_request(request)

    try:
        result = _dispatch(action, client_ip)
    except Exception as exc:
        logger.error("%-32s %s  -> 500  %s", action, client_ip, exc, exc_info=True)
        return soap_fault(reason=str(exc))

    logger.info("%-32s %s  -> 200 OK (%.1fms)", action, client_ip, t.ms())
    return result


def _dispatch(action: str, client_ip: str):
    handlers = {
        "GetImagingSettings": _get_imaging_settings,
        "GetOptions":         _get_options,
        "SetImagingSettings": _stub_empty,
        "GetStatus":          _get_status,
        "GetMoveOptions":     _stub_empty,
        "Move":               _stub_empty,
        "Stop":               _stub_empty,
    }
    handler = handlers.get(action)
    if handler is None:
        logger.warning("%-32s %s  -> 400 ActionNotSupported", action, client_ip)
        return action_not_supported(action)
    return handler()


def _get_imaging_settings():
    cfg  = current_app.config["CFG"]["imaging"]
    timg = NS["timg"]
    tt   = NS["tt"]

    resp = etree.Element(f"{{{timg}}}GetImagingSettingsResponse")
    settings = etree.SubElement(resp, f"{{{timg}}}ImagingSettings")
    etree.SubElement(settings, f"{{{tt}}}Brightness").text  = str(cfg["brightness"])
    etree.SubElement(settings, f"{{{tt}}}ColorSaturation").text = str(cfg["saturation"])
    etree.SubElement(settings, f"{{{tt}}}Contrast").text    = str(cfg["contrast"])
    return soap_response(resp)


def _get_options():
    timg = NS["timg"]
    tt   = NS["tt"]

    resp = etree.Element(f"{{{timg}}}GetOptionsResponse")
    opts = etree.SubElement(resp, f"{{{timg}}}ImagingOptions")
    for field in ("Brightness", "ColorSaturation", "Contrast", "Sharpness"):
        r = etree.SubElement(opts, f"{{{tt}}}{field}")
        etree.SubElement(r, f"{{{tt}}}Min").text  = "0"
        etree.SubElement(r, f"{{{tt}}}Max").text  = "100"
    return soap_response(resp)


def _get_status():
    timg = NS["timg"]
    tt   = NS["tt"]

    resp = etree.Element(f"{{{timg}}}GetStatusResponse")
    status = etree.SubElement(resp, f"{{{timg}}}Status")
    etree.SubElement(status, f"{{{tt}}}FocusStatus20").text = "Idle"
    return soap_response(resp)


def _stub_empty():
    action = soap_action_from_request(request)
    timg = NS["timg"]
    resp = etree.Element(f"{{{timg}}}{action}Response")
    return soap_response(resp)
