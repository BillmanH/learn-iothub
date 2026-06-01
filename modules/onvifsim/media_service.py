"""
media_service.py — ONVIF Media service blueprint.
"""
from __future__ import annotations

import logging

from flask import Blueprint, current_app, request
from lxml import etree

from soap_utils import (
    NS, Timer, action_not_supported, soap_action_from_request,
    soap_fault, soap_response,
)

logger = logging.getLogger("onvifsim.media_service")

media_bp = Blueprint("media_service", __name__)

_PROFILE_TOKEN = "profile_0"
_SOURCE_TOKEN  = "video_0"
_ENCODER_TOKEN = "encoder_0"
_SOURCE_CONFIG_TOKEN = "vsconfig_0"


def _host_url() -> str:
    import socket
    cfg = current_app.config["CFG"]
    port = cfg["server"]["port"]
    try:
        host = socket.gethostbyname(socket.gethostname())
    except Exception:
        host = "127.0.0.1"
    return f"http://{host}:{port}"


@media_bp.route("/onvif/media_service", methods=["POST"])
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
    logger.info("%-32s %s  -> 200 OK (%.1fms)", action, client_ip, elapsed)
    return result


def _dispatch(action: str, client_ip: str):
    handlers = {
        # Tier 1
        "GetProfiles":                          _get_profiles,
        "GetProfile":                           _get_profile,
        "GetVideoSources":                      _get_video_sources,
        "GetVideoSourceConfigurations":         _get_video_source_configurations,
        "GetVideoEncoderConfigurations":        _get_video_encoder_configurations,
        "GetStreamUri":                         _get_stream_uri,
        "GetSnapshotUri":                       _get_snapshot_uri,
        # Tier 2 stubs
        "GetVideoEncoderConfigurationOptions":  _stub_encoder_options,
        "GetCompatibleVideoEncoderConfigurations": _get_video_encoder_configurations,
        "GetAudioSources":                      _stub_empty,
        "GetAudioSourceConfigurations":         _stub_empty,
        "GetAudioEncoderConfigurations":        _stub_empty,
        "GetMetadataConfigurations":            _stub_empty,
        "StartMulticastStreaming":              _stub_empty,
        "StopMulticastStreaming":               _stub_empty,
        "CreateProfile":                        _stub_empty,
        "DeleteProfile":                        _stub_empty,
        "SetVideoSourceConfiguration":         _stub_empty,
        "SetVideoEncoderConfiguration":        _stub_empty,
        "SetAudioSourceConfiguration":         _stub_empty,
        "SetAudioEncoderConfiguration":        _stub_empty,
        "SetMetadataConfiguration":            _stub_empty,
        "AddVideoSourceConfiguration":         _stub_empty,
        "AddVideoEncoderConfiguration":        _stub_empty,
        "RemoveVideoSourceConfiguration":      _stub_empty,
        "RemoveVideoEncoderConfiguration":     _stub_empty,
    }
    handler = handlers.get(action)
    if handler is None:
        logger.warning("%-32s %s  -> 400 ActionNotSupported", action, client_ip)
        return action_not_supported(action)
    return handler()


# ---------------------------------------------------------------------------
# Tier 1
# ---------------------------------------------------------------------------

def _profile_element(parent, with_ns=True):
    """Build a tt:Profiles element and append to parent."""
    cfg = current_app.config["CFG"]
    tt  = NS["tt"]
    width  = cfg["media"]["video_resolution"]["width"]
    height = cfg["media"]["video_resolution"]["height"]
    fps    = cfg["media"]["frame_rate"]

    profile = etree.SubElement(
        parent, f"{{{tt}}}Profiles",
        attrib={"token": _PROFILE_TOKEN, "fixed": "false"},
    )
    etree.SubElement(profile, f"{{{tt}}}Name").text = "MainStream"

    vsc = etree.SubElement(profile, f"{{{tt}}}VideoSourceConfiguration",
                           attrib={"token": _SOURCE_CONFIG_TOKEN})
    etree.SubElement(vsc, f"{{{tt}}}Name").text            = "VideoSourceConfig"
    etree.SubElement(vsc, f"{{{tt}}}UseCount").text        = "1"
    etree.SubElement(vsc, f"{{{tt}}}SourceToken").text     = _SOURCE_TOKEN
    bounds = etree.SubElement(vsc, f"{{{tt}}}Bounds",
                              attrib={"x": "0", "y": "0",
                                      "width": str(width), "height": str(height)})

    vec = etree.SubElement(profile, f"{{{tt}}}VideoEncoderConfiguration",
                           attrib={"token": _ENCODER_TOKEN})
    etree.SubElement(vec, f"{{{tt}}}Name").text     = "H264Config"
    etree.SubElement(vec, f"{{{tt}}}UseCount").text = "1"
    etree.SubElement(vec, f"{{{tt}}}Encoding").text = "H264"
    res = etree.SubElement(vec, f"{{{tt}}}Resolution")
    etree.SubElement(res, f"{{{tt}}}Width").text  = str(width)
    etree.SubElement(res, f"{{{tt}}}Height").text = str(height)
    etree.SubElement(vec, f"{{{tt}}}Quality").text      = "5.0"
    rate = etree.SubElement(vec, f"{{{tt}}}RateControl")
    etree.SubElement(rate, f"{{{tt}}}FrameRateLimit").text   = str(fps)
    etree.SubElement(rate, f"{{{tt}}}EncodingInterval").text = "1"
    etree.SubElement(rate, f"{{{tt}}}BitrateLimit").text     = "4096"
    h264 = etree.SubElement(vec, f"{{{tt}}}H264")
    etree.SubElement(h264, f"{{{tt}}}GovLength").text   = str(fps)
    etree.SubElement(h264, f"{{{tt}}}H264Profile").text = "Main"
    return profile


def _get_profiles():
    trt = NS["trt"]
    resp = etree.Element(f"{{{trt}}}GetProfilesResponse")
    _profile_element(resp)
    return soap_response(resp)


def _get_profile():
    trt = NS["trt"]
    resp = etree.Element(f"{{{trt}}}GetProfileResponse")
    _profile_element(resp)
    return soap_response(resp)


def _get_video_sources():
    cfg = current_app.config["CFG"]
    tt  = NS["tt"]
    trt = NS["trt"]
    width  = cfg["media"]["video_resolution"]["width"]
    height = cfg["media"]["video_resolution"]["height"]
    fps    = cfg["media"]["frame_rate"]

    resp = etree.Element(f"{{{trt}}}GetVideoSourcesResponse")
    vs = etree.SubElement(resp, f"{{{trt}}}VideoSources",
                          attrib={"token": _SOURCE_TOKEN})
    etree.SubElement(vs, f"{{{tt}}}Framerate").text = str(fps)
    res = etree.SubElement(vs, f"{{{tt}}}Resolution")
    etree.SubElement(res, f"{{{tt}}}Width").text  = str(width)
    etree.SubElement(res, f"{{{tt}}}Height").text = str(height)
    return soap_response(resp)


def _get_video_source_configurations():
    cfg = current_app.config["CFG"]
    tt  = NS["tt"]
    trt = NS["trt"]
    width  = cfg["media"]["video_resolution"]["width"]
    height = cfg["media"]["video_resolution"]["height"]

    resp = etree.Element(f"{{{trt}}}GetVideoSourceConfigurationsResponse")
    vsc = etree.SubElement(resp, f"{{{trt}}}Configurations",
                           attrib={"token": _SOURCE_CONFIG_TOKEN})
    etree.SubElement(vsc, f"{{{tt}}}Name").text        = "VideoSourceConfig"
    etree.SubElement(vsc, f"{{{tt}}}UseCount").text    = "1"
    etree.SubElement(vsc, f"{{{tt}}}SourceToken").text = _SOURCE_TOKEN
    etree.SubElement(vsc, f"{{{tt}}}Bounds",
                     attrib={"x": "0", "y": "0",
                             "width": str(width), "height": str(height)})
    return soap_response(resp)


def _get_video_encoder_configurations():
    cfg = current_app.config["CFG"]
    tt  = NS["tt"]
    trt = NS["trt"]
    width  = cfg["media"]["video_resolution"]["width"]
    height = cfg["media"]["video_resolution"]["height"]
    fps    = cfg["media"]["frame_rate"]

    resp = etree.Element(f"{{{trt}}}GetVideoEncoderConfigurationsResponse")
    vec = etree.SubElement(resp, f"{{{trt}}}Configurations",
                           attrib={"token": _ENCODER_TOKEN})
    etree.SubElement(vec, f"{{{tt}}}Name").text     = "H264Config"
    etree.SubElement(vec, f"{{{tt}}}UseCount").text = "1"
    etree.SubElement(vec, f"{{{tt}}}Encoding").text = "H264"
    res = etree.SubElement(vec, f"{{{tt}}}Resolution")
    etree.SubElement(res, f"{{{tt}}}Width").text  = str(width)
    etree.SubElement(res, f"{{{tt}}}Height").text = str(height)
    etree.SubElement(vec, f"{{{tt}}}Quality").text = "5.0"
    rate = etree.SubElement(vec, f"{{{tt}}}RateControl")
    etree.SubElement(rate, f"{{{tt}}}FrameRateLimit").text   = str(fps)
    etree.SubElement(rate, f"{{{tt}}}EncodingInterval").text = "1"
    etree.SubElement(rate, f"{{{tt}}}BitrateLimit").text     = "4096"
    return soap_response(resp)


def _get_stream_uri():
    host = _host_url()
    trt = NS["trt"]
    tt  = NS["tt"]

    resp = etree.Element(f"{{{trt}}}GetStreamUriResponse")
    muri = etree.SubElement(resp, f"{{{trt}}}MediaUri")
    etree.SubElement(muri, f"{{{tt}}}Uri").text             = f"{host}/stream"
    etree.SubElement(muri, f"{{{tt}}}InvalidAfterConnect").text = "false"
    etree.SubElement(muri, f"{{{tt}}}InvalidAfterReboot").text  = "false"
    etree.SubElement(muri, f"{{{tt}}}Timeout").text         = "PT0S"
    return soap_response(resp)


def _get_snapshot_uri():
    host = _host_url()
    trt = NS["trt"]
    tt  = NS["tt"]

    resp = etree.Element(f"{{{trt}}}GetSnapshotUriResponse")
    muri = etree.SubElement(resp, f"{{{trt}}}MediaUri")
    etree.SubElement(muri, f"{{{tt}}}Uri").text             = f"{host}/snapshot"
    etree.SubElement(muri, f"{{{tt}}}InvalidAfterConnect").text = "false"
    etree.SubElement(muri, f"{{{tt}}}InvalidAfterReboot").text  = "false"
    etree.SubElement(muri, f"{{{tt}}}Timeout").text         = "PT0S"
    return soap_response(resp)


# ---------------------------------------------------------------------------
# Tier 2 stubs
# ---------------------------------------------------------------------------

def _stub_empty():
    action = soap_action_from_request(request)
    trt = NS["trt"]
    resp = etree.Element(f"{{{trt}}}{action}Response")
    return soap_response(resp)


def _stub_encoder_options():
    cfg = current_app.config["CFG"]
    tt  = NS["tt"]
    trt = NS["trt"]
    width  = cfg["media"]["video_resolution"]["width"]
    height = cfg["media"]["video_resolution"]["height"]

    resp = etree.Element(f"{{{trt}}}GetVideoEncoderConfigurationOptionsResponse")
    opts = etree.SubElement(resp, f"{{{trt}}}Options")
    h264 = etree.SubElement(opts, f"{{{tt}}}H264")
    resolutions = etree.SubElement(h264, f"{{{tt}}}ResolutionsAvailable")
    res = etree.SubElement(resolutions, f"{{{tt}}}Width")
    res.text = str(width)
    res2 = etree.SubElement(resolutions, f"{{{tt}}}Height")
    res2.text = str(height)
    etree.SubElement(h264, f"{{{tt}}}FrameRateRange").text  = ""
    etree.SubElement(h264, f"{{{tt}}}EncodingIntervalRange").text = ""
    return soap_response(resp)
