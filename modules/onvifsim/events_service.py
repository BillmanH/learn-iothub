"""
events_service.py — ONVIF Events service blueprint (pull-point subscriptions).
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, request
from lxml import etree

from soap_utils import (
    NS, Timer, action_not_supported, soap_action_from_request,
    soap_fault, soap_response,
)

logger = logging.getLogger("onvifsim.events_service")

events_bp = Blueprint("events_service", __name__)

# In-memory subscription store
_subs_lock = threading.Lock()
_subscriptions: dict[str, dict] = {}

_DEFAULT_TTL_SECONDS = 60


def _new_sub_id() -> str:
    return f"sub_{uuid.uuid4().hex[:8]}"


def _expiry_str(seconds: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return exp.strftime("%Y-%m-%dT%H:%M:%SZ")


@events_bp.route("/onvif/events_service", methods=["POST"])
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
        "GetServiceCapabilities":        _get_service_capabilities,
        "GetEventProperties":            _get_event_properties,
        "CreatePullPointSubscription":   _create_pull_point_subscription,
        "PullMessages":                  _pull_messages,
        "Renew":                         _renew,
        "Unsubscribe":                   _unsubscribe,
    }
    handler = handlers.get(action)
    if handler is None:
        logger.warning("%-32s %s  -> 400 ActionNotSupported", action, client_ip)
        return action_not_supported(action)
    return handler()


def _get_service_capabilities():
    tev = NS["tev"]
    resp = etree.Element(f"{{{tev}}}GetServiceCapabilitiesResponse")
    caps = etree.SubElement(resp, f"{{{tev}}}Capabilities",
                            attrib={"WSSubscriptionPolicySupport": "false",
                                    "WSPullPointSupport": "true",
                                    "WSPausableSubscriptionManagerInterfaceSupport": "false",
                                    "MaxNotificationProducers": "1",
                                    "MaxPullPoints": "10",
                                    "PersistentNotificationStorage": "false"})
    return soap_response(resp)


def _get_event_properties():
    tev = NS["tev"]
    resp = etree.Element(f"{{{tev}}}GetEventPropertiesResponse")
    etree.SubElement(resp, f"{{{tev}}}TopicNamespaceLocation").text = (
        "http://www.onvif.org/onvif/ver10/topics/topicns.xml"
    )
    etree.SubElement(resp, f"{{{tev}}}FixedTopicSet").text = "true"
    etree.SubElement(resp, f"{{{tev}}}TopicSet")
    etree.SubElement(resp, f"{{{tev}}}TopicExpressionDialect").text = (
        "http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet"
    )
    return soap_response(resp)


def _create_pull_point_subscription():
    host = _host_url()
    sub_id = _new_sub_id()
    expiry = _expiry_str(_DEFAULT_TTL_SECONDS)

    with _subs_lock:
        _subscriptions[sub_id] = {
            "id": sub_id,
            "expires": expiry,
        }

    tev = NS["tev"]
    wsa = NS["wsa"]
    wsnt = NS["wsnt"]

    resp = etree.Element(f"{{{tev}}}CreatePullPointSubscriptionResponse")
    epr  = etree.SubElement(resp, f"{{{tev}}}SubscriptionReference")
    etree.SubElement(epr, f"{{{wsa}}}Address").text = f"{host}/onvif/events_service"
    ref_params = etree.SubElement(epr, f"{{{wsa}}}ReferenceParameters")
    sub_id_el = etree.SubElement(ref_params, f"{{{tev}}}SubscriptionId")
    sub_id_el.text = sub_id
    etree.SubElement(resp, f"{{{wsnt}}}CurrentTime").text          = _expiry_str(0).replace("+00:00", "Z")
    etree.SubElement(resp, f"{{{wsnt}}}TerminationTime").text      = expiry

    logger.info("CreatePullPointSubscription -> subscriptionId=%s expires=PT%dS", sub_id, _DEFAULT_TTL_SECONDS)
    return soap_response(resp)


def _pull_messages():
    sub_id = _extract_sub_id()
    tev   = NS["tev"]
    wsnt  = NS["wsnt"]
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    resp = etree.Element(f"{{{tev}}}PullMessagesResponse")
    etree.SubElement(resp, f"{{{tev}}}CurrentTime").text     = now
    etree.SubElement(resp, f"{{{tev}}}TerminationTime").text = _expiry_str(_DEFAULT_TTL_SECONDS)
    # No events generated — return empty list

    logger.info("PullMessages subscriptionId=%s -> 0 messages", sub_id or "unknown")
    return soap_response(resp)


def _renew():
    sub_id = _extract_sub_id()
    expiry = _expiry_str(_DEFAULT_TTL_SECONDS)
    if sub_id:
        with _subs_lock:
            if sub_id in _subscriptions:
                _subscriptions[sub_id]["expires"] = expiry

    wsnt = NS["wsnt"]
    resp = etree.Element(f"{{{wsnt}}}RenewResponse")
    etree.SubElement(resp, f"{{{wsnt}}}TerminationTime").text = expiry
    etree.SubElement(resp, f"{{{wsnt}}}CurrentTime").text     = _expiry_str(0)
    return soap_response(resp)


def _unsubscribe():
    sub_id = _extract_sub_id()
    if sub_id:
        with _subs_lock:
            _subscriptions.pop(sub_id, None)
    wsnt = NS["wsnt"]
    resp = etree.Element(f"{{{wsnt}}}UnsubscribeResponse")
    return soap_response(resp)


def _extract_sub_id() -> str | None:
    """Try to extract SubscriptionId from the WS-Addressing ReferenceParameters."""
    try:
        from soap_utils import parse_body
        root = etree.fromstring(request.data)
        tev = NS["tev"]
        wsa = NS["wsa"]
        # Look in header first (WS-Addressing)
        header = root.find(f"{{{NS['s']}}}Header")
        if header is not None:
            el = header.find(f".//{{{tev}}}SubscriptionId")
            if el is not None:
                return el.text
    except Exception:
        pass
    return None


def _host_url() -> str:
    import socket
    cfg = current_app.config["CFG"]
    port = cfg["server"]["port"]
    try:
        host = socket.gethostbyname(socket.gethostname())
    except Exception:
        host = "127.0.0.1"
    return f"http://{host}:{port}"
