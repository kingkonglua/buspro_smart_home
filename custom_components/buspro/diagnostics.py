"""Diagnostics support for the Buspro integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import DATA_BUSPRO
from .const import CONF_DEVICES

TO_REDACT = {CONF_HOST}


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

    module = hass.data.get(DATA_BUSPRO)
    if module is not None:
        hdl = getattr(module, "hdl", None)
        gateway: dict[str, Any] = {
            "connected": getattr(module, "connected", None),
        }
        send_receive = getattr(module, "gateway_address_send_receive", None)
        if send_receive is not None:
            gateway["gateway_address_send_receive"] = [
                list(send_receive[0]),
                list(send_receive[1]),
            ]
        if hdl is not None:
            gateway["advertised_ip"] = getattr(hdl, "advertised_ip", None)
            gateway["allowed_source_ips"] = sorted(
                getattr(hdl, "allowed_source_ips", set()) or []
            )
            gateway["dropped_source_ips"] = sorted(
                getattr(hdl, "dropped_source_ips", set()) or []
            )
            gateway["started"] = getattr(hdl, "started", None)
        diag["gateway"] = async_redact_data(
            gateway, {"host", "gateway_address_send_receive", "advertised_ip"}
        )

    return diag
