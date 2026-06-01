"""
device_service.py — ONVIF Device service blueprint.

Implements all operations from the ONVIF Core Spec v2.5 Device service.
Tier 1 ops return real data from config; Tier 2 stubs return minimal valid
responses; Tier 3 returns ter:ActionNotSupported.
"""
from __future__ import annotations

import logging
import socket
import uuid
from datetime import datetime, timezone

from flask import Blueprint, request
from lxml import etree

from soap_utils import (
    NS, Timer, action_not_supported, soap_action_from_request,
    soap_fault, soap_response, sub, sub_tt,
)

logger = logging.getLogger("onvifsim.device_service")

device_bp = Blueprint("device_service", __name__)

# SOAPActions handled by this blueprint
_TIER3 = {
    "UpgradeSystemFirmware", "StartFirmwareUpgrade",
    "StartSystemRestore", "GetSystemBackup",
    "SetNetworkInterfaces",
}


def _cfg(key_path: str):
    """Read from app config by dot-separated path into cfg dict."""
    from flask import current_app
    cfg = current_app.config["CFG"]
    parts = key_path.split(".")
    val = cfg
    for p in parts:
        val = val[p]
    return val


def _host_url() -> str:
    from flask import current_app
    port = current_app.config["CFG"]["server"]["port"]
    try:
        hostname = socket.gethostname()
        host = socket.gethostbyname(hostname)
    except Exception:
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def _device_serial_guid() -> str:
    from flask import current_app
    serial = current_app.config["CFG"]["device"]["serial"]
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, serial))


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@device_bp.route("/onvif/device_service", methods=["POST"])
def handle():
    t = Timer()
    client_ip = request.remote_addr or "-"
    action = soap_action_from_request(request)

    if action in _TIER3:
        logger.warning("%-32s %s  -> 400 ActionNotSupported", action, client_ip)
        return action_not_supported(action)

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
        "GetSystemDateAndTime":    _get_system_date_and_time,
        "GetDeviceInformation":    _get_device_information,
        "GetCapabilities":         _get_capabilities,
        "GetServices":             _get_services,
        "GetServiceCapabilities":  _get_service_capabilities,
        "GetScopes":               _get_scopes,
        "GetHostname":             _get_hostname,
        "GetEndpointReference":    _get_endpoint_reference,
        "GetUsers":                _get_users,
        # Tier 2
        "SetSystemDateAndTime":    _stub_empty,
        "SystemReboot":            _system_reboot,
        "SetScopes":               _stub_empty,
        "AddScopes":               _stub_empty,
        "RemoveScopes":            _stub_empty,
        "GetDiscoveryMode":        _get_discovery_mode,
        "SetDiscoveryMode":        _stub_empty,
        "GetRemoteDiscoveryMode":  _get_discovery_mode,
        "SetRemoteDiscoveryMode":  _stub_empty,
        "GetDNS":                  _get_dns,
        "SetDNS":                  _stub_empty,
        "GetNTP":                  _get_ntp,
        "SetNTP":                  _stub_empty,
        "GetNetworkInterfaces":    _get_network_interfaces,
        "GetNetworkProtocols":     _get_network_protocols,
        "GetNetworkDefaultGateway": _get_network_default_gateway,
        "SetHostname":             _stub_empty,
        "GetZeroConfiguration":    _get_zero_configuration,
        "GetDynamicDNS":           _get_dynamic_dns,
        "GetWsdlUrl":              _get_wsdl_url,
        "GetSystemLog":            _get_system_log,
        "GetSystemSupportInformation": _get_system_support_information,
        "CreateUsers":             _stub_empty,
        "DeleteUsers":             _stub_empty,
        "SetUser":                 _stub_empty,
        "GetRelayOutputs":         _get_relay_outputs,
        "GetIPAddressFilter":      _get_ip_address_filter,
        "SetIPAddressFilter":      _stub_empty,
        "GetDot11Capabilities":    _get_dot11_capabilities,
        "GetDot11Status":          _get_dot11_status,
        "GetStorageConfigurations": _get_storage_configurations,
        "GetGeoLocation":          _get_geo_location,
        # Dot1X / Certificate / AccessPolicy stubs
        "GetDot1XConfigurations":  _stub_empty,
        "GetDot1XConfiguration":   _stub_empty,
        "SetDot1XConfiguration":   _stub_empty,
        "DeleteDot1XConfiguration": _stub_empty,
        "GetCACertificates":       _stub_empty,
        "GetPkcs10Request":        _stub_empty,
        "GetClientCertificateMode": _stub_empty,
        "SetClientCertificateMode": _stub_empty,
        "LoadCertificates":        _stub_empty,
        "DeleteCertificates":      _stub_empty,
        "GetCertificateInformation": _stub_empty,
        "GetAccessPolicy":         _stub_empty,
        "SetAccessPolicy":         _stub_empty,
    }
    handler = handlers.get(action)
    if handler is None:
        logger.warning("%-32s %s  -> 400 ActionNotSupported (unknown)", action, client_ip)
        return action_not_supported(action)
    return handler()


# ---------------------------------------------------------------------------
# Tier 1 handlers
# ---------------------------------------------------------------------------

def _get_system_date_and_time():
    now = datetime.now(timezone.utc)
    tds = NS["tds"]
    tt  = NS["tt"]

    resp = etree.Element(f"{{{tds}}}GetSystemDateAndTimeResponse")
    sdt  = etree.SubElement(resp, f"{{{tt}}}SystemDateAndTime")
    etree.SubElement(sdt, f"{{{tt}}}DateTimeType").text    = "NTP"
    etree.SubElement(sdt, f"{{{tt}}}DaylightSavings").text = "false"
    tz = etree.SubElement(sdt, f"{{{tt}}}TimeZone")
    etree.SubElement(tz, f"{{{tt}}}TZ").text = "UTC"
    utc = etree.SubElement(sdt, f"{{{tt}}}UTCDateTime")
    t_el = etree.SubElement(utc, f"{{{tt}}}Time")
    etree.SubElement(t_el, f"{{{tt}}}Hour").text   = str(now.hour)
    etree.SubElement(t_el, f"{{{tt}}}Minute").text = str(now.minute)
    etree.SubElement(t_el, f"{{{tt}}}Second").text = str(now.second)
    d_el = etree.SubElement(utc, f"{{{tt}}}Date")
    etree.SubElement(d_el, f"{{{tt}}}Year").text  = str(now.year)
    etree.SubElement(d_el, f"{{{tt}}}Month").text = str(now.month)
    etree.SubElement(d_el, f"{{{tt}}}Day").text   = str(now.day)
    return soap_response(resp)


def _get_device_information():
    from flask import current_app
    cfg = current_app.config["CFG"]["device"]
    tds = NS["tds"]
    tt  = NS["tt"]

    resp = etree.Element(f"{{{tds}}}GetDeviceInformationResponse")
    etree.SubElement(resp, f"{{{tt}}}Manufacturer").text    = cfg["manufacturer"]
    etree.SubElement(resp, f"{{{tt}}}Model").text           = cfg["model"]
    etree.SubElement(resp, f"{{{tt}}}FirmwareVersion").text = cfg["firmware_version"]
    etree.SubElement(resp, f"{{{tt}}}SerialNumber").text    = cfg["serial"]
    etree.SubElement(resp, f"{{{tt}}}HardwareId").text      = cfg["hardware_id"]
    return soap_response(resp)


def _get_capabilities():
    host = _host_url()
    tds = NS["tds"]
    tt  = NS["tt"]

    resp = etree.Element(f"{{{tds}}}GetCapabilitiesResponse")
    caps = etree.SubElement(resp, f"{{{tt}}}Capabilities")

    analytics = etree.SubElement(caps, f"{{{tt}}}Analytics")
    etree.SubElement(analytics, f"{{{tt}}}XAddr").text           = f"{host}/onvif/analytics_service"
    etree.SubElement(analytics, f"{{{tt}}}RuleSupport").text     = "false"
    etree.SubElement(analytics, f"{{{tt}}}AnalyticsModuleSupport").text = "false"

    device = etree.SubElement(caps, f"{{{tt}}}Device")
    etree.SubElement(device, f"{{{tt}}}XAddr").text = f"{host}/onvif/device_service"
    network = etree.SubElement(device, f"{{{tt}}}Network")
    etree.SubElement(network, f"{{{tt}}}IPFilter").text         = "false"
    etree.SubElement(network, f"{{{tt}}}ZeroConfiguration").text = "false"
    etree.SubElement(network, f"{{{tt}}}IPVersion6").text       = "false"
    etree.SubElement(network, f"{{{tt}}}DynDNS").text           = "false"
    io = etree.SubElement(device, f"{{{tt}}}IO")
    etree.SubElement(io, f"{{{tt}}}InputConnectors").text = "0"
    etree.SubElement(io, f"{{{tt}}}RelayOutputs").text    = "0"
    system = etree.SubElement(device, f"{{{tt}}}System")
    etree.SubElement(system, f"{{{tt}}}DiscoveryResolve").text  = "false"
    etree.SubElement(system, f"{{{tt}}}DiscoveryBye").text      = "false"
    etree.SubElement(system, f"{{{tt}}}RemoteDiscovery").text   = "false"
    etree.SubElement(system, f"{{{tt}}}SystemBackup").text      = "false"
    etree.SubElement(system, f"{{{tt}}}SystemLogging").text     = "false"
    etree.SubElement(system, f"{{{tt}}}FirmwareUpgrade").text   = "false"
    security = etree.SubElement(device, f"{{{tt}}}Security")
    etree.SubElement(security, f"{{{tt}}}TLS1.2").text         = "false"
    etree.SubElement(security, f"{{{tt}}}OnboardKeyGeneration").text = "false"
    etree.SubElement(security, f"{{{tt}}}AccessPolicyConfig").text  = "false"
    etree.SubElement(security, f"{{{tt}}}X.509Token").text          = "false"
    etree.SubElement(security, f"{{{tt}}}SAMLToken").text           = "false"
    etree.SubElement(security, f"{{{tt}}}KerberosToken").text       = "false"
    etree.SubElement(security, f"{{{tt}}}UsernameToken").text       = "true"
    etree.SubElement(security, f"{{{tt}}}HttpDigest").text          = "false"
    etree.SubElement(security, f"{{{tt}}}RELToken").text            = "false"

    events = etree.SubElement(caps, f"{{{tt}}}Events")
    etree.SubElement(events, f"{{{tt}}}XAddr").text                   = f"{host}/onvif/events_service"
    etree.SubElement(events, f"{{{tt}}}WSSubscriptionPolicySupport").text = "false"
    etree.SubElement(events, f"{{{tt}}}WSPullPointSupport").text          = "true"
    etree.SubElement(events, f"{{{tt}}}WSPausableSubscriptionManagerInterfaceSupport").text = "false"

    imaging = etree.SubElement(caps, f"{{{tt}}}Imaging")
    etree.SubElement(imaging, f"{{{tt}}}XAddr").text = f"{host}/onvif/imaging_service"

    media = etree.SubElement(caps, f"{{{tt}}}Media")
    etree.SubElement(media, f"{{{tt}}}XAddr").text = f"{host}/onvif/media_service"
    streaming = etree.SubElement(media, f"{{{tt}}}StreamingCapabilities")
    etree.SubElement(streaming, f"{{{tt}}}RTPMulticast").text = "false"
    etree.SubElement(streaming, f"{{{tt}}}RTP_TCP").text      = "true"
    etree.SubElement(streaming, f"{{{tt}}}RTP_RTSP_TCP").text = "false"

    ptz = etree.SubElement(caps, f"{{{tt}}}PTZ")
    etree.SubElement(ptz, f"{{{tt}}}XAddr").text = f"{host}/onvif/ptz_service"

    return soap_response(resp)


def _get_services():
    host = _host_url()
    tds = NS["tds"]
    tt  = NS["tt"]

    services = [
        ("http://www.onvif.org/ver10/device/wsdl",   f"{host}/onvif/device_service",  "2.42"),
        ("http://www.onvif.org/ver10/media/wsdl",    f"{host}/onvif/media_service",   "2.6"),
        ("http://www.onvif.org/ver20/ptz/wsdl",      f"{host}/onvif/ptz_service",     "2.4"),
        ("http://www.onvif.org/ver20/imaging/wsdl",  f"{host}/onvif/imaging_service", "2.3"),
        ("http://www.onvif.org/ver10/events/wsdl",   f"{host}/onvif/events_service",  "2.6"),
    ]

    resp = etree.Element(f"{{{tds}}}GetServicesResponse")
    for ns_uri, xaddr, version in services:
        svc = etree.SubElement(resp, f"{{{tds}}}Service")
        etree.SubElement(svc, f"{{{tds}}}Namespace").text = ns_uri
        etree.SubElement(svc, f"{{{tds}}}XAddr").text     = xaddr
        ver = etree.SubElement(svc, f"{{{tds}}}Version")
        major, minor = version.split(".")
        etree.SubElement(ver, f"{{{tt}}}Major").text = major
        etree.SubElement(ver, f"{{{tt}}}Minor").text = minor
    return soap_response(resp)


def _get_service_capabilities():
    from flask import current_app
    auth_mode = current_app.config["CFG"]["auth"]["mode"].lower()
    tds = NS["tds"]

    resp = etree.Element(f"{{{tds}}}GetServiceCapabilitiesResponse")
    caps = etree.SubElement(resp, f"{{{tds}}}Capabilities")
    network = etree.SubElement(caps, f"{{{tds}}}Network",
        attrib={"IPFilter": "false", "ZeroConfiguration": "false",
                "IPVersion6": "false", "DynDNS": "false"})
    system = etree.SubElement(caps, f"{{{tds}}}System",
        attrib={"DiscoveryResolve": "false", "DiscoveryBye": "false",
                "RemoteDiscovery": "false", "SystemBackup": "false",
                "SystemLogging": "false", "FirmwareUpgrade": "false",
                "MaxSupportedVersions": "2"})
    security = etree.SubElement(caps, f"{{{tds}}}Security",
        attrib={
            "TLS1.2": "false",
            "OnboardKeyGeneration": "false",
            "AccessPolicyConfig": "false",
            "X.509Token": "false",
            "SAMLToken": "false",
            "KerberosToken": "false",
            "UsernameToken": "true" if auth_mode != "none" else "false",
            "HttpDigest": "false",
            "RELToken": "false",
        })
    return soap_response(resp)


def _get_scopes():
    from flask import current_app
    name = current_app.config["CFG"]["device"]["name"]
    tds = NS["tds"]
    tt  = NS["tt"]

    scopes_text = [
        "onvif://www.onvif.org/type/video_encoder",
        f"onvif://www.onvif.org/hardware/{name}",
        f"onvif://www.onvif.org/name/{name}",
    ]
    resp = etree.Element(f"{{{tds}}}GetScopesResponse")
    for s in scopes_text:
        scope = etree.SubElement(resp, f"{{{tds}}}Scopes")
        etree.SubElement(scope, f"{{{tt}}}ScopeDef").text  = "Fixed"
        etree.SubElement(scope, f"{{{tt}}}ScopeItem").text = s
    return soap_response(resp)


def _get_hostname():
    from flask import current_app
    name = current_app.config["CFG"]["device"]["name"]
    tds = NS["tds"]
    tt  = NS["tt"]

    resp = etree.Element(f"{{{tds}}}GetHostnameResponse")
    info = etree.SubElement(resp, f"{{{tds}}}HostnameInformation")
    etree.SubElement(info, f"{{{tt}}}FromDHCP").text = "false"
    etree.SubElement(info, f"{{{tt}}}Name").text      = name
    return soap_response(resp)


def _get_endpoint_reference():
    guid = _device_serial_guid()
    tds = NS["tds"]
    wsa = NS["wsa"]

    resp = etree.Element(f"{{{tds}}}GetEndpointReferenceResponse")
    epr  = etree.SubElement(resp, f"{{{tds}}}GUID")
    epr.text = guid
    return soap_response(resp)


def _get_users():
    from flask import current_app
    username = current_app.config["CFG"]["auth"]["username"]
    tds = NS["tds"]
    tt  = NS["tt"]

    resp = etree.Element(f"{{{tds}}}GetUsersResponse")
    user = etree.SubElement(resp, f"{{{tds}}}User")
    etree.SubElement(user, f"{{{tt}}}Username").text  = username
    etree.SubElement(user, f"{{{tt}}}UserLevel").text = "Administrator"
    return soap_response(resp)


# ---------------------------------------------------------------------------
# Tier 2 handlers
# ---------------------------------------------------------------------------

def _stub_empty():
    action = soap_action_from_request(request)
    tds = NS["tds"]
    resp = etree.Element(f"{{{tds}}}{action}Response")
    return soap_response(resp)


def _system_reboot():
    tds = NS["tds"]
    resp = etree.Element(f"{{{tds}}}SystemRebootResponse")
    etree.SubElement(resp, f"{{{tds}}}Message").text = "Simulated reboot"
    return soap_response(resp)


def _get_discovery_mode():
    action = soap_action_from_request(request)
    tds = NS["tds"]
    tt  = NS["tt"]
    resp = etree.Element(f"{{{tds}}}{action}Response")
    etree.SubElement(resp, f"{{{tds}}}DiscoveryMode").text = "Discoverable"
    return soap_response(resp)


def _get_dns():
    tds = NS["tds"]
    tt  = NS["tt"]
    resp = etree.Element(f"{{{tds}}}GetDNSResponse")
    info = etree.SubElement(resp, f"{{{tds}}}DNSInformation")
    etree.SubElement(info, f"{{{tt}}}FromDHCP").text = "false"
    return soap_response(resp)


def _get_ntp():
    tds = NS["tds"]
    tt  = NS["tt"]
    resp = etree.Element(f"{{{tds}}}GetNTPResponse")
    info = etree.SubElement(resp, f"{{{tds}}}NTPInformation")
    etree.SubElement(info, f"{{{tt}}}FromDHCP").text = "false"
    return soap_response(resp)


def _get_network_interfaces():
    tds = NS["tds"]
    tt  = NS["tt"]
    resp = etree.Element(f"{{{tds}}}GetNetworkInterfacesResponse")
    iface = etree.SubElement(resp, f"{{{tds}}}NetworkInterfaces", attrib={"token": "eth0"})
    enabled = etree.SubElement(iface, f"{{{tt}}}Enabled")
    enabled.text = "true"
    info = etree.SubElement(iface, f"{{{tt}}}Info")
    etree.SubElement(info, f"{{{tt}}}Name").text     = "eth0"
    etree.SubElement(info, f"{{{tt}}}HwAddress").text = "00:00:00:00:00:00"
    ipv4 = etree.SubElement(iface, f"{{{tt}}}IPv4")
    etree.SubElement(ipv4, f"{{{tt}}}Enabled").text = "true"
    config = etree.SubElement(ipv4, f"{{{tt}}}Config")
    manual = etree.SubElement(config, f"{{{tt}}}Manual")
    etree.SubElement(manual, f"{{{tt}}}Address").text      = "127.0.0.1"
    etree.SubElement(manual, f"{{{tt}}}PrefixLength").text = "8"
    etree.SubElement(config, f"{{{tt}}}DHCP").text         = "false"
    return soap_response(resp)


def _get_network_protocols():
    from flask import current_app
    port = current_app.config["CFG"]["server"]["port"]
    tds  = NS["tds"]
    tt   = NS["tt"]
    resp = etree.Element(f"{{{tds}}}GetNetworkProtocolsResponse")
    proto = etree.SubElement(resp, f"{{{tds}}}NetworkProtocols")
    etree.SubElement(proto, f"{{{tt}}}Name").text    = "HTTP"
    etree.SubElement(proto, f"{{{tt}}}Enabled").text = "true"
    etree.SubElement(proto, f"{{{tt}}}Port").text    = str(port)
    return soap_response(resp)


def _get_network_default_gateway():
    tds = NS["tds"]
    tt  = NS["tt"]
    resp = etree.Element(f"{{{tds}}}GetNetworkDefaultGatewayResponse")
    gw = etree.SubElement(resp, f"{{{tds}}}NetworkGateway")
    etree.SubElement(gw, f"{{{tt}}}IPv4Address").text = "0.0.0.0"
    return soap_response(resp)


def _get_zero_configuration():
    tds = NS["tds"]
    tt  = NS["tt"]
    resp = etree.Element(f"{{{tds}}}GetZeroConfigurationResponse")
    zc = etree.SubElement(resp, f"{{{tds}}}ZeroConfiguration")
    etree.SubElement(zc, f"{{{tt}}}InterfaceToken").text = "eth0"
    etree.SubElement(zc, f"{{{tt}}}Enabled").text        = "false"
    return soap_response(resp)


def _get_dynamic_dns():
    tds = NS["tds"]
    tt  = NS["tt"]
    resp = etree.Element(f"{{{tds}}}GetDynamicDNSResponse")
    info = etree.SubElement(resp, f"{{{tds}}}DynamicDNSInformation")
    etree.SubElement(info, f"{{{tt}}}Type").text = "NoUpdate"
    return soap_response(resp)


def _get_wsdl_url():
    tds = NS["tds"]
    resp = etree.Element(f"{{{tds}}}GetWsdlUrlResponse")
    etree.SubElement(resp, f"{{{tds}}}WsdlUrl").text = (
        "https://www.onvif.org/ver10/device/wsdl/devicemgmt.wsdl"
    )
    return soap_response(resp)


def _get_system_log():
    tds = NS["tds"]
    tt  = NS["tt"]
    resp = etree.Element(f"{{{tds}}}GetSystemLogResponse")
    log  = etree.SubElement(resp, f"{{{tds}}}SystemLog")
    etree.SubElement(log, f"{{{tt}}}String").text = ""
    return soap_response(resp)


def _get_system_support_information():
    tds = NS["tds"]
    tt  = NS["tt"]
    resp = etree.Element(f"{{{tds}}}GetSystemSupportInformationResponse")
    info = etree.SubElement(resp, f"{{{tds}}}SupportInformation")
    etree.SubElement(info, f"{{{tt}}}String").text = "Simulated device"
    return soap_response(resp)


def _get_relay_outputs():
    tds = NS["tds"]
    resp = etree.Element(f"{{{tds}}}GetRelayOutputsResponse")
    return soap_response(resp)


def _get_ip_address_filter():
    tds = NS["tds"]
    tt  = NS["tt"]
    resp = etree.Element(f"{{{tds}}}GetIPAddressFilterResponse")
    f    = etree.SubElement(resp, f"{{{tds}}}IPAddressFilter")
    etree.SubElement(f, f"{{{tt}}}Type").text = "Allow"
    return soap_response(resp)


def _get_dot11_capabilities():
    tds = NS["tds"]
    tt  = NS["tt"]
    resp = etree.Element(f"{{{tds}}}GetDot11CapabilitiesResponse")
    caps = etree.SubElement(resp, f"{{{tds}}}Capabilities")
    for field in ("TKIP", "ScanAvailableNetworks", "MultipleConfiguration",
                  "AdHocStationMode", "WEP"):
        etree.SubElement(caps, f"{{{tt}}}{field}").text = "false"
    return soap_response(resp)


def _get_dot11_status():
    tds = NS["tds"]
    resp = etree.Element(f"{{{tds}}}GetDot11StatusResponse")
    etree.SubElement(resp, f"{{{tds}}}Status")
    return soap_response(resp)


def _get_storage_configurations():
    tds = NS["tds"]
    resp = etree.Element(f"{{{tds}}}GetStorageConfigurationsResponse")
    return soap_response(resp)


def _get_geo_location():
    tds = NS["tds"]
    resp = etree.Element(f"{{{tds}}}GetGeoLocationResponse")
    return soap_response(resp)
