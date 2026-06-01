"""
soap_utils.py — shared SOAP envelope builder, parser, fault generator, and SOAPAction router.
"""
from __future__ import annotations

import time
from lxml import etree

# ---------------------------------------------------------------------------
# Namespace constants
# ---------------------------------------------------------------------------
NS = {
    "s":    "http://www.w3.org/2003/05/soap-envelope",
    "wsse": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
    "wsu":  "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd",
    "wsa":  "http://www.w3.org/2005/08/addressing",
    "tt":   "http://www.onvif.org/ver10/schema",
    "tds":  "http://www.onvif.org/ver10/device/wsdl",
    "trt":  "http://www.onvif.org/ver10/media/wsdl",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
    "timg": "http://www.onvif.org/ver20/imaging/wsdl",
    "tev":  "http://www.onvif.org/ver10/events/wsdl",
    "ter":  "http://www.onvif.org/ver10/error",
    "wsnt": "http://docs.oasis-open.org/wsn/b-2",
}

_SOAP_ENV   = NS["s"]
_SOAP_BODY  = f"{{{_SOAP_ENV}}}Body"
_SOAP_HDR   = f"{{{_SOAP_ENV}}}Header"

CONTENT_TYPE = "application/soap+xml; charset=utf-8"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_body(xml_bytes: bytes) -> etree._Element:
    """Parse raw request bytes and return the first child of the SOAP Body."""
    root = etree.fromstring(xml_bytes)
    body = root.find(_SOAP_BODY)
    if body is None or len(body) == 0:
        raise ValueError("No SOAP Body element found")
    return body[0]


def soap_action_from_request(flask_request) -> str:
    """
    Extract the bare operation name from the SOAPAction header or the
    first-child local name of the SOAP Body.

    Prefers SOAPAction header (strip namespace prefix and quotes), falls
    back to body element local name so bare POST requests also work.
    """
    action_header = flask_request.headers.get("SOAPAction", "")
    if action_header:
        # Strip surrounding quotes and namespace URI, keep local name
        action_header = action_header.strip('"').strip("'")
        if "/" in action_header:
            return action_header.rsplit("/", 1)[-1]
        return action_header

    # Fallback: parse body
    try:
        body_child = parse_body(flask_request.data)
        return etree.QName(body_child).localname
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------

def soap_response(body_element: etree._Element, status: int = 200):
    """Wrap body_element in a SOAP Envelope and return (xml_bytes, status, headers)."""
    envelope = etree.Element(f"{{{_SOAP_ENV}}}Envelope", nsmap=NS)
    body = etree.SubElement(envelope, f"{{{_SOAP_ENV}}}Body")
    body.append(body_element)
    xml_bytes = etree.tostring(envelope, xml_declaration=True, encoding="utf-8")
    return xml_bytes, status, {"Content-Type": CONTENT_TYPE}


# ---------------------------------------------------------------------------
# Fault builder
# ---------------------------------------------------------------------------

def soap_fault(
    code: str = "s:Receiver",
    subcode: str | None = None,
    reason: str = "Internal error",
    status: int = 500,
):
    """
    Build a SOAP 1.2 Fault response.

    Common subcodes (use the ter: prefix, e.g. 'ter:ActionNotSupported'):
      ter:ActionNotSupported  — unknown/unsupported operation
      ter:NotAuthorized       — authentication failed
      ter:InvalidArgVal       — bad argument value
    """
    envelope = etree.Element(f"{{{_SOAP_ENV}}}Envelope", nsmap=NS)
    body = etree.SubElement(envelope, f"{{{_SOAP_ENV}}}Body")
    fault = etree.SubElement(body, f"{{{_SOAP_ENV}}}Fault")

    fault_code = etree.SubElement(fault, f"{{{_SOAP_ENV}}}Code")
    etree.SubElement(fault_code, f"{{{_SOAP_ENV}}}Value").text = code

    if subcode:
        sub = etree.SubElement(fault_code, f"{{{_SOAP_ENV}}}Subcode")
        etree.SubElement(sub, f"{{{_SOAP_ENV}}}Value").text = subcode

    fault_reason = etree.SubElement(fault, f"{{{_SOAP_ENV}}}Reason")
    etree.SubElement(
        fault_reason,
        f"{{{_SOAP_ENV}}}Text",
        attrib={"{http://www.w3.org/XML/1998/namespace}lang": "en"},
    ).text = reason

    xml_bytes = etree.tostring(envelope, xml_declaration=True, encoding="utf-8")
    return xml_bytes, status, {"Content-Type": CONTENT_TYPE}


def action_not_supported(action: str):
    return soap_fault(
        code="s:Sender",
        subcode="ter:ActionNotSupported",
        reason=f"Action not supported: {action}",
        status=400,
    )


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def el(tag: str, ns: str, text: str | None = None, parent=None, attrib=None) -> etree._Element:
    """Create an element with namespace, optional text and optional parent."""
    e = etree.Element(f"{{{ns}}}{tag}", attrib=attrib or {})
    if text is not None:
        e.text = str(text)
    if parent is not None:
        parent.append(e)
    return e


def sub(parent: etree._Element, tag: str, ns: str, text: str | None = None, attrib=None) -> etree._Element:
    """SubElement shorthand with namespace."""
    e = etree.SubElement(parent, f"{{{ns}}}{tag}", attrib=attrib or {})
    if text is not None:
        e.text = str(text)
    return e


def tt(tag: str, text: str | None = None, parent=None, attrib=None) -> etree._Element:
    """Shorthand for the onvif schema namespace (tt:)."""
    return el(tag, NS["tt"], text=text, parent=parent, attrib=attrib)


def sub_tt(parent, tag: str, text: str | None = None, attrib=None) -> etree._Element:
    return sub(parent, tag, NS["tt"], text=text, attrib=attrib)


# ---------------------------------------------------------------------------
# WS-Security header parser (used by auth.py)
# ---------------------------------------------------------------------------

def parse_wssecurity(xml_bytes: bytes) -> dict | None:
    """
    Extract WS-Security UsernameToken fields from the SOAP Header.

    Returns dict with keys: username, password, password_type, nonce, created
    or None if no WS-Security header is present.
    """
    try:
        root = etree.fromstring(xml_bytes)
        header = root.find(_SOAP_HDR)
        if header is None:
            return None

        wsse_ns = NS["wsse"]
        security = header.find(f"{{{wsse_ns}}}Security")
        if security is None:
            return None

        token = security.find(f"{{{wsse_ns}}}UsernameToken")
        if token is None:
            return None

        username_el = token.find(f"{{{wsse_ns}}}Username")
        password_el = token.find(f"{{{wsse_ns}}}Password")
        nonce_el    = token.find(f"{{{wsse_ns}}}Nonce")
        created_el  = token.find(f"{{{NS['wsu']}}}Created")

        password_type = ""
        if password_el is not None:
            pt = password_el.get("Type", "")
            if pt:
                password_type = pt.rsplit("#", 1)[-1]  # e.g. "PasswordDigest"

        return {
            "username":      (username_el.text or "") if username_el is not None else "",
            "password":      (password_el.text or "") if password_el is not None else "",
            "password_type": password_type,
            "nonce":         (nonce_el.text or "")    if nonce_el    is not None else "",
            "created":       (created_el.text or "")  if created_el  is not None else "",
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Timing helper used by services for latency logging
# ---------------------------------------------------------------------------

class Timer:
    def __init__(self):
        self._start = time.perf_counter()

    def ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000
