"""Diagnostics support for the Buspro integration."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import DATA_BUSPRO, get_buspro_module
from .const import CONF_DEVICES

TO_REDACT = {CONF_HOST}
GATEWAY_REDACT = {"host", "gateway_address_send_receive", "advertised_ip"}

# Keys whose value is a *sequence* of internal source IPs. ``async_redact_data``
# only replaces whole dict keys and never rewrites the members of a list/set,
# so these cannot be added to GATEWAY_REDACT (that would collapse the list into
# a single scalar) and are blanked element-wise by :func:`_redact_ips` instead.
GATEWAY_SEQUENCE_REDACT = ("allowed_source_ips", "dropped_source_ips")

REDACTED = "**REDACTED**"
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _redact_ips(value: Any) -> Any:
    """Recursively blank every IPv4 literal, preserving the container shape.

    R7: the diagnostics payload must never export an internal address, wherever
    it hides — the ``allowed_source_ips`` / ``dropped_source_ips`` lists (which
    ``async_redact_data`` cannot reach inside), the peer addresses, or an IP
    embedded in ``entry.unique_id`` (``"host:port"``). A dotted-quad becomes
    ``**REDACTED**`` while a list stays a list, so the diagnostics remain
    structurally usable.
    """
    if isinstance(value, str):
        return REDACTED if _IPV4_RE.search(value) else value
    if isinstance(value, dict):
        return {key: _redact_ips(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_ips(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_redact_ips(item) for item in value)
    return value


def _gateway_info(module) -> dict[str, Any]:
    """Describe a single gateway module."""
    gateway: dict[str, Any] = {
        "connected": getattr(module, "connected", None),
    }
    send_receive = getattr(module, "gateway_address_send_receive", None)
    if send_receive is not None:
        gateway["gateway_address_send_receive"] = [
            list(send_receive[0]),
            list(send_receive[1]),
        ]
    hdl = getattr(module, "hdl", None)
    if hdl is not None:
        gateway["advertised_ip"] = getattr(hdl, "advertised_ip", None)
        gateway["allowed_source_ips"] = sorted(
            getattr(hdl, "allowed_source_ips", set()) or []
        )
        gateway["dropped_source_ips"] = sorted(
            getattr(hdl, "dropped_source_ips", set()) or []
        )
        gateway["started"] = getattr(hdl, "started", None)
    # The source-IP lists are sequences; redact their members explicitly.
    for key in GATEWAY_SEQUENCE_REDACT:
        if key in gateway:
            gateway[key] = _redact_ips(gateway[key])
    return async_redact_data(gateway, GATEWAY_REDACT)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    diag: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
            "unique_id": entry.unique_id,
        },
        "devices": list((entry.options.get(CONF_DEVICES) or {}).keys()),
    }

    # M-8: report every configured gateway, not just the (previously proxied)
    # first one, keyed by config-entry id.
    bucket = hass.data.get(DATA_BUSPRO)
    if isinstance(bucket, dict):
        items = list(bucket.items())
    elif bucket is not None:
        items = [(entry.entry_id, bucket)]
    else:
        items = []

    gateways: dict[str, Any] = {}
    for entry_id, module in items:
        if module is None:
            continue
        gateways[entry_id] = _gateway_info(module)

    if gateways:
        diag["gateways"] = gateways

    current = get_buspro_module(hass, entry.entry_id)
    if current is not None:
        diag["gateway"] = gateways.get(entry.entry_id) or _gateway_info(current)

    # R7 defence in depth: nothing in the exported payload may contain a
    # plaintext internal address, in any path (entry unique_id/title, options,
    # per-gateway blocks, ...).
    return _redact_ips(diag)
