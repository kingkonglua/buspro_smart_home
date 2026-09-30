"""Diagnostics support for the Buspro integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import DATA_BUSPRO, get_buspro_module
from .const import CONF_DEVICES

TO_REDACT = {CONF_HOST}
GATEWAY_REDACT = {"host", "gateway_address_send_receive", "advertised_ip"}


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

    return diag
