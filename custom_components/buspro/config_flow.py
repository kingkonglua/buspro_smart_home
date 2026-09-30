"""
Config flow for HDL Buspro integration.

Handles initial gateway setup (host/port) and device management via OptionsFlow.

Two convenience entry points are layered on top of the classic manual flow
(both are optional -- manual IP entry keeps working exactly as before):

- Gateway auto-discovery: on first launch the integration probes UDP/6000
  broadcast for HDL gateways and offers a dropdown; picking one prefills the
  existing host/port form.
- Bus scan: from the options menu, a broadcast scan discovers every device on
  the bus (relays, dimmers, sensors, floor heating, dry contacts, curtains and
  AC units), classified automatically, for one-click import.
"""

import asyncio
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

try:
    # ``SelectOptionDict`` is a TypedDict that newer HA builds expose for
    # select options; older builds only accept plain dicts. Falling back keeps
    # config_flow importable (an ImportError here would make the whole
    # integration fail with SETUP_ERROR before any entity is created).
    from homeassistant.helpers.selector import SelectOptionDict
except ImportError:  # pragma: no cover - version-compat shim
    SelectOptionDict = dict

from . import get_buspro_module
from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_DEVICES,
    CONF_DEVICE_TYPE,
    CONF_SUBNET_ID,
    CONF_DEVICE_ID,
    CONF_CHANNEL,
    CONF_SUBTYPE,
    CONF_AC_NUMBER,
    CONF_TRAVEL_TIME,
    CONF_AREA_NUMBER,
    CONF_SCENE_NUMBER,
    DEVICE_TYPES,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_SWITCH,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_SENSOR,
    DEVICE_TYPE_CLIMATE,
    DEVICE_TYPE_COVER,
    DEVICE_TYPE_BUTTON,
    DEVICE_TYPE_SCENE,
    BINARY_SENSOR_SUBTYPES,
    SENSOR_SUBTYPES,
    COVER_SUBTYPES,
    CLIMATE_SUBTYPES,
    DEFAULT_PORT,
    CONF_GATEWAY_CHOICE,
    CHOICE_MANUAL,
    CHOICE_RESCAN,
    GATEWAY_DISCOVERY_TIMEOUT,
    CONF_SCAN_DURATION,
    DEFAULT_SCAN_DURATION,
    MIN_SCAN_DURATION,
    MAX_SCAN_DURATION,
    SCAN_DEVICES_SELECTION,
)

_LOGGER = logging.getLogger(__name__)


class BusproConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for HDL Buspro gateway setup."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self._discovered_gateways: dict[str, dict] | None = None
        self._prefill: dict = {}

    async def _async_run_gateway_discovery(self) -> dict[str, dict]:
        """Probe UDP/6000 for HDL gateways on the local network."""
        # Imported lazily so a problem in the discovery helper can never break
        # the import of the whole config flow module.
        from .gateway_discovery import async_discover_gateways

        try:
            self._discovered_gateways = await async_discover_gateways(
                self.hass, timeout=GATEWAY_DISCOVERY_TIMEOUT
            )
        except Exception as err:  # noqa: BLE001 - discovery must never block setup
            _LOGGER.warning("HDL gateway auto-detection failed: %s", err)
            self._discovered_gateways = {}
        return self._discovered_gateways

    async def async_step_user(self, user_input=None):
        """Handle the initial step.

        First invocation runs a quick gateway discovery and shows a dropdown of
        everything that answered. The user can pick a discovered IP (which
        prefills the classic form below), rescan, or choose manual entry.
        If nothing is found, we fall straight through to the classic form --
        the hand-filled-IP path stays exactly as it was.
        """
        if user_input is not None:
            choice = user_input.get(CONF_GATEWAY_CHOICE)
            if choice == CHOICE_MANUAL:
                return await self.async_step_manual()
            if choice == CHOICE_RESCAN:
                self._discovered_gateways = None
                return await self.async_step_user()
            # A discovered gateway IP: prefill the manual form with it.
            info = (self._discovered_gateways or {}).get(choice, {})
            self._prefill = {
                CONF_HOST: choice,
                CONF_PORT: int(info.get("port", DEFAULT_PORT)),
            }
            return await self.async_step_manual()

        if self._discovered_gateways is None:
            await self._async_run_gateway_discovery()

        gateways = self._discovered_gateways or {}
        if not gateways:
            # Nothing answered the broadcast probe (different L2 segment,
            # blocked broadcasts, ...) -- fall back to the manual form.
            return await self.async_step_manual()

        options = [
            SelectOptionDict(
                value=ip, label=info.get("label", ip)
            )
            for ip, info in gateways.items()
        ]
        options.append(
            SelectOptionDict(value=CHOICE_RESCAN, label="Scan again")
        )
        options.append(
            SelectOptionDict(
                value=CHOICE_MANUAL, label="Enter address manually"
            )
        )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_GATEWAY_CHOICE,
                    default=next(iter(gateways)),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }),
            description_placeholders={"count": str(len(gateways))},
        )

    async def async_step_manual(self, user_input=None):
        """Handle the classic gateway setup form (hand-filled IP)."""
        errors = {}

        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
            )
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title="HDL Buspro",
                data=user_input,
            )

        schema = {
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT): int,
        }
        if self._prefill:
            schema = {
                vol.Required(
                    CONF_HOST, default=self._prefill.get(CONF_HOST, vol.UNDEFINED)
                ): str,
                vol.Required(
                    CONF_PORT, default=self._prefill.get(CONF_PORT, DEFAULT_PORT)
                ): int,
            }

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return BusproOptionsFlow()


class BusproOptionsFlow(config_entries.OptionsFlow):
    """Handle the options flow for managing Buspro devices."""

    def __init__(self):
        """Initialize options flow."""
        self.devices = None
        self._scan_results = None
        self._scan_task = None
        self._scan_started = 0.0
        self._scan_duration = DEFAULT_SCAN_DURATION
        self._scan_cleanup_registered = False

    def _cancel_scan_task(self):
        """Cancel a still-running bus scan (flow closed/cancelled/re-entered).

        Without this, closing the options dialog mid-scan leaves the task
        running to completion (bounded by its own watchdog) with no owner; it
        is registered on the flow via async_on_remove so HA runs it when the
        flow finishes.
        """
        if self._scan_task is not None and not self._scan_task.done():
            self._scan_task.cancel()
        self._scan_task = None

    def _ensure_devices_loaded(self):
        """Lazy load devices from config entry options."""
        if self.devices is None:
            self.devices = dict(self.config_entry.options.get(CONF_DEVICES, {}))

    def _get_device_display_name(self, device_key, device_config):
        """Get a human-readable display string for a device."""
        name = device_config.get("name", device_key)
        subnet = device_config.get(CONF_SUBNET_ID, "")
        device_id = device_config.get(CONF_DEVICE_ID, "")
        channel = device_config.get(CONF_CHANNEL)
        device_type = device_config.get(CONF_DEVICE_TYPE, "")

        if channel is not None:
            addr = f"{subnet}.{device_id}.{channel}"
        else:
            addr = f"{subnet}.{device_id}"

        return f"{name} ({device_type}, {addr})"

    def _live_hdl(self):
        """Return the connected Buspro client for this entry, or None."""
        module = get_buspro_module(self.hass, self.config_entry.entry_id)
        hdl = getattr(module, "hdl", None)
        if hdl is None or getattr(hdl, "network_interface", None) is None:
            return None
        return hdl

    async def async_step_init(self, user_input=None):
        """Show the main menu: add / scan / remove / done."""
        self._ensure_devices_loaded()

        if user_input is not None:
            action = user_input.get("action")
            if action == "add":
                return await self.async_step_add_device()
            elif action == "scan_bus":
                return await self.async_step_scan_bus()
            elif action == "remove":
                return await self.async_step_remove_device()
            elif action == "done":
                return self.async_create_entry(
                    title="",
                    data={CONF_DEVICES: self.devices},
                )

        # Build device list summary
        if self.devices:
            summary = "\n".join([
                f"  - {self._get_device_display_name(k, v)}"
                for k, v in self.devices.items()
            ])
        else:
            summary = "  (none)"

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required("action"): vol.In([
                    "add",
                    "scan_bus",
                    "remove",
                    "done",
                ]),
            }),
            description_placeholders={"devices": summary},
        )

    # ----- bus scan: discover devices automatically ------------------------
    async def async_step_scan_bus(self, user_input=None):
        """Scan the bus for devices, with a live countdown while it runs."""
        from .discovery import SCAN_TIMEOUT_MARGIN, BusScanner

        if self._scan_task is None and user_input is not None:
            hdl = self._live_hdl()
            if hdl is None:
                return self.async_abort(reason="gateway_unavailable")

            self._scan_duration = int(
                user_input.get(CONF_SCAN_DURATION, DEFAULT_SCAN_DURATION)
            )

            scanner = BusScanner(hdl)
            self._scan_started = self.hass.loop.time()
            # The real scan runs independently in the background; the flow
            # only needs to know when it's done to move on. The watchdog
            # allows for the directed follow-up phase beyond the listen
            # window (see discovery.SCAN_TIMEOUT_MARGIN).
            self._scan_task = self.hass.async_create_task(
                asyncio.wait_for(
                    scanner.scan(self._scan_duration),
                    timeout=self._scan_duration + SCAN_TIMEOUT_MARGIN,
                ),
                name=f"{DOMAIN} bus scan",
            )
            if not self._scan_cleanup_registered:
                on_remove = getattr(self, "async_on_remove", None)
                if on_remove is not None:
                    on_remove(self._cancel_scan_task)
                self._scan_cleanup_registered = True

        if self._scan_task is not None:
            if not self._scan_task.done():
                elapsed = self.hass.loop.time() - self._scan_started
                # A throwaway 1s task just paces the tick - it's not the scan
                # itself, so a slow render doesn't delay the scan and the scan
                # finishing early doesn't wait on this tick.
                tick = self.hass.async_create_task(asyncio.sleep(1))
                if elapsed < self._scan_duration:
                    seconds_left = max(0, round(self._scan_duration - elapsed))
                    return self.async_show_progress(
                        step_id="scan_bus",
                        progress_action="bus_scan",
                        description_placeholders={
                            "seconds_left": str(seconds_left)
                        },
                        progress_task=tick,
                    )
                # Listen window over; the scanner is now doing its directed
                # follow-up reads to confirm channel counts. Show a distinct
                # message instead of freezing the countdown at 0.
                return self.async_show_progress(
                    step_id="scan_bus",
                    progress_action="bus_scan_confirming",
                    progress_task=tick,
                )

            task, self._scan_task = self._scan_task, None
            try:
                self._scan_results = task.result()
            except (asyncio.TimeoutError, RuntimeError, OSError) as err:
                _LOGGER.warning("Buspro bus scan failed: %s", err)
                return self.async_abort(reason="scan_failed")

            if not self._scan_results:
                return self.async_show_progress_done(
                    next_step_id="scan_bus_no_devices"
                )
            return self.async_show_progress_done(next_step_id="scan_bus_devices")

        # No scan running yet: ask for the listen duration.
        return self.async_show_form(
            step_id="scan_bus",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_SCAN_DURATION, default=DEFAULT_SCAN_DURATION
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_SCAN_DURATION,
                        max=MAX_SCAN_DURATION,
                        step=1,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
            }),
        )

    async def async_step_scan_bus_no_devices(self, user_input=None):
        """Abort after a scan that found nothing."""
        return self.async_abort(reason="no_devices_found")

    # ----- scan results: checklist import ----------------------------------
    def _infer_type(self, disc):
        """Classify a discovered device (lazy import of the scanner module)."""
        from .discovery import SCAN_TYPE_LABELS, infer_device_type

        return infer_device_type(disc), SCAN_TYPE_LABELS

    def _existing_triples(self):
        """(type, subnet, device, channel-ish) tuples already configured."""
        triples = set()
        for cfg in self.devices.values():
            triples.add((
                cfg.get(CONF_DEVICE_TYPE),
                cfg.get(CONF_SUBNET_ID),
                cfg.get(CONF_DEVICE_ID),
                cfg.get(CONF_CHANNEL),
            ))
        return triples

    def _import_discovered(self, disc, classification, *, subtype_override: str | None = None):
        """Build fully-populated device configs for one discovered module.

        Every field the entity platforms need (subnet/device/channel/type/
        subtype/...) is filled in here -- the user only ticks checkboxes.
        Multi-channel modules are split into per-channel entries (per-channel
        lights/switches, per-curtain covers, per-unit AC climates).
        """
        from .const import DEVICE_TYPE_AC, DEVICE_TYPE_CURTAIN

        s, d = disc.subnet_id, disc.device_id
        count = disc.channel_count or 1
        configs = []
        ops = disc.op_codes

        if classification == DEVICE_TYPE_CURTAIN:
            subtype = "curtain_module"
            for ch in range(1, max(count, 1) + 1):
                key = f"{DEVICE_TYPE_COVER}_{s}_{d}_{ch}_{subtype}"
                configs.append((
                    key,
                    {
                        CONF_DEVICE_TYPE: DEVICE_TYPE_COVER,
                        CONF_SUBNET_ID: s,
                        CONF_DEVICE_ID: d,
                        CONF_CHANNEL: ch,
                        CONF_SUBTYPE: subtype,
                        CONF_TRAVEL_TIME: 15,
                        "name": f"HDL {disc.address} curtain{ch}",
                    },
                ))
        elif classification == DEVICE_TYPE_AC:
            subtype = "ac"
            for ac_number in range(1, max(count, 1) + 1):
                key = f"{DEVICE_TYPE_CLIMATE}_{s}_{d}_{subtype}_{ac_number}"
                configs.append((
                    key,
                    {
                        CONF_DEVICE_TYPE: DEVICE_TYPE_CLIMATE,
                        CONF_SUBNET_ID: s,
                        CONF_DEVICE_ID: d,
                        CONF_SUBTYPE: subtype,
                        CONF_AC_NUMBER: ac_number,
                        "name": f"HDL {disc.address} AC{ac_number}",
                    },
                ))
        elif classification == DEVICE_TYPE_CLIMATE:
            subtype = "floor_heating"
            key = f"{DEVICE_TYPE_CLIMATE}_{s}_{d}_{subtype}_1"
            configs.append((
                key,
                {
                    CONF_DEVICE_TYPE: DEVICE_TYPE_CLIMATE,
                    CONF_SUBNET_ID: s,
                    CONF_DEVICE_ID: d,
                    CONF_SUBTYPE: subtype,
                    CONF_AC_NUMBER: 1,
                    "name": f"HDL {disc.address} heating",
                },
            ))
        elif classification == DEVICE_TYPE_SENSOR:
            # Subtype may be overridden by the scan_bus_devices form
            subtype = subtype_override if subtype_override is not None else (
                "temperature" if "temperature" in SENSOR_SUBTYPES
                else SENSOR_SUBTYPES[0]
            )
            key = f"{DEVICE_TYPE_SENSOR}_{s}_{d}_{subtype}"
            configs.append((
                key,
                {
                    CONF_DEVICE_TYPE: DEVICE_TYPE_SENSOR,
                    CONF_SUBNET_ID: s,
                    CONF_DEVICE_ID: d,
                    CONF_SUBTYPE: subtype,
                    "name": f"HDL {disc.address} sensor",
                },
            ))
        elif classification == DEVICE_TYPE_BINARY_SENSOR:
            # Universal-switch modules vs dry-contact modules, told apart by
            # which probe they answered.
            if "ReadStatusOfUniversalSwitchResponse" in ops:
                subtype = (
                    "universal_switch"
                    if "universal_switch" in BINARY_SENSOR_SUBTYPES
                    else BINARY_SENSOR_SUBTYPES[0]
                )
            else:
                subtype = (
                    "dry_contact"
                    if "dry_contact" in BINARY_SENSOR_SUBTYPES
                    else BINARY_SENSOR_SUBTYPES[0]
                )
            key = f"{DEVICE_TYPE_BINARY_SENSOR}_{s}_{d}_1_{subtype}"
            configs.append((
                key,
                {
                    CONF_DEVICE_TYPE: DEVICE_TYPE_BINARY_SENSOR,
                    CONF_SUBNET_ID: s,
                    CONF_DEVICE_ID: d,
                    CONF_CHANNEL: 1,
                    CONF_SUBTYPE: subtype,
                    "name": f"HDL {disc.address} binary_sensor",
                },
            ))
        else:
            # switch / light: split into one entry per known channel.
            dtype = (
                classification if classification in (DEVICE_TYPE_LIGHT, DEVICE_TYPE_SWITCH)
                else DEVICE_TYPE_SWITCH
            )
            for ch in range(1, max(count, 1) + 1):
                key = f"{dtype}_{s}_{d}_{ch}"
                configs.append((
                    key,
                    {
                        CONF_DEVICE_TYPE: dtype,
                        CONF_SUBNET_ID: s,
                        CONF_DEVICE_ID: d,
                        CONF_CHANNEL: ch,
                        "name": f"HDL {disc.address} ch{ch}",
                    },
                ))
        return configs

    async def async_step_scan_bus_devices(self, user_input=None):
        """Let the user tick which discovered devices to import.

        For sensor-type devices an extra subtype dropdown is shown per sensor
        (temperature vs. illuminance) so the right entity class is created.
        """
        from .const import DEVICE_TYPE_AC, DEVICE_TYPE_CURTAIN, DEVICE_TYPE_SENSOR

        results = self._scan_results
        if not results:
            return self.async_abort(reason="no_devices_found")

        # Pre-classify all results
        classified: list[tuple] = []  # (disc, classification, label, new_keys)
        options: list[SelectOptionDict] = []
        default_selected: list[str] = []
        for disc in results:
            classification, labels = self._infer_type(disc)
            if classification is None:
                continue
            configs = self._import_discovered(disc, classification)
            new_keys = [k for k, _ in configs if k not in self.devices]
            parts = [disc.address, labels.get(classification, classification)]
            if disc.type_code and disc.type_code != "0x0000":
                parts.append(disc.type_code)
            if disc.channel_count:
                parts.append(f"{disc.channel_count}ch")
            label = "  ·  ".join(parts)
            if not new_keys:
                label += "  ✓"
            else:
                default_selected.extend(new_keys)
            options.append(SelectOptionDict(value=disc.key, label=label))
            classified.append((disc, classification, label, new_keys))

        if not classified:
            return self.async_abort(reason="no_devices_found")

        # Identify sensor entries for subtype selection
        sensor_entries = [(disc, new_keys) for disc, cls, _, new_keys in classified
                          if cls == DEVICE_TYPE_SENSOR]

        if user_input is not None:
            chosen = set(user_input.get(SCAN_DEVICES_SELECTION, []))
            sensor_subtypes = user_input.get("sensor_subtypes", {})
            by_disc_key = {d.key: d for d in results}
            by_classification = {d.key: c for d, c, _, _ in classified}
            added = 0
            for disc_key in chosen:
                disc = by_disc_key.get(disc_key)
                if disc is None:
                    continue
                classification = by_classification.get(disc_key)
                if classification is None:
                    continue
                # For sensors, override the default subtype with user's choice
                subtype_override = None
                if classification == DEVICE_TYPE_SENSOR:
                    subtype_override = sensor_subtypes.get(disc_key, "temperature")
                for dev_key, dev_cfg in self._import_discovered(
                    disc, classification, subtype_override=subtype_override
                ):
                    if dev_key in self.devices:
                        continue
                    self.devices[dev_key] = dev_cfg
                    added += 1
            _LOGGER.info("Bus scan import: added %d device(s)", added)
            return self.async_create_entry(
                title="",
                data={CONF_DEVICES: self.devices},
            )

        # Build form with optional per-sensor subtype field
        form_schema: dict = {
            vol.Optional(
                SCAN_DEVICES_SELECTION,
                default=default_selected,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    mode=selector.SelectSelectorMode.LIST,
                    multiple=True,
                )
            ),
        }
        if sensor_entries:
            sensor_subtype_schema: dict = {}
            for disc, _new_keys in sensor_entries:
                sensor_subtype_schema[vol.Optional(disc.key, default="temperature")] = (
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                "temperature",
                                "illuminance",
                                "humidity",
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                )
            form_schema["sensor_subtypes"] = vol.Schema(sensor_subtype_schema)

        return self.async_show_form(
            step_id="scan_bus_devices",
            data_schema=vol.Schema(form_schema),
            description_placeholders={
                "found": str(len(classified)),
                "new": str(len(default_selected)),
            },
        )

    # ----- manual device management (unchanged) ----------------------------
    async def async_step_add_device(self, user_input=None):
        """Step: select device type."""
        if user_input is not None:
            device_type = user_input[CONF_DEVICE_TYPE]
            if device_type == DEVICE_TYPE_LIGHT:
                return await self.async_step_add_light()
            elif device_type == DEVICE_TYPE_SWITCH:
                return await self.async_step_add_switch()
            elif device_type == DEVICE_TYPE_BINARY_SENSOR:
                return await self.async_step_add_binary_sensor()
            elif device_type == DEVICE_TYPE_SENSOR:
                return await self.async_step_add_sensor()
            elif device_type == DEVICE_TYPE_CLIMATE:
                return await self.async_step_add_climate()
            elif device_type == DEVICE_TYPE_COVER:
                return await self.async_step_add_cover()
            elif device_type == DEVICE_TYPE_BUTTON:
                return await self.async_step_add_button()
            elif device_type == DEVICE_TYPE_SCENE:
                return await self.async_step_add_scene()

        return self.async_show_form(
            step_id="add_device",
            data_schema=vol.Schema({
                vol.Required(CONF_DEVICE_TYPE): vol.In(DEVICE_TYPES),
            }),
        )

    async def async_step_add_light(self, user_input=None):
        """Step: add a light device."""
        errors = {}
        device_key_label = ""
        if user_input is not None:
            subnet = user_input[CONF_SUBNET_ID]
            dev_id = user_input[CONF_DEVICE_ID]
            channel = user_input[CONF_CHANNEL]
            key = f"{DEVICE_TYPE_LIGHT}_{subnet}_{dev_id}_{channel}"

            if key in self.devices:
                errors["base"] = "already_exists"
                device_key_label = self._get_device_display_name(
                    key, self.devices[key]
                )
            else:
                self.devices[key] = {
                    CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT,
                    CONF_SUBNET_ID: subnet,
                    CONF_DEVICE_ID: dev_id,
                    CONF_CHANNEL: channel,
                    "name": user_input.get("name", f"Light {subnet}-{dev_id}-{channel}"),
                }
                return await self.async_step_init()

        return self.async_show_form(
            step_id="add_light",
            data_schema=vol.Schema({
                vol.Required(CONF_SUBNET_ID): int,
                vol.Required(CONF_DEVICE_ID): int,
                vol.Required(CONF_CHANNEL): int,
                vol.Optional("name", default=""): str,
            }),
            errors=errors,
            description_placeholders={"device_key": device_key_label},
        )

    async def async_step_add_switch(self, user_input=None):
        """Step: add a switch device."""
        errors = {}
        device_key_label = ""
        if user_input is not None:
            subnet = user_input[CONF_SUBNET_ID]
            dev_id = user_input[CONF_DEVICE_ID]
            channel = user_input[CONF_CHANNEL]
            key = f"{DEVICE_TYPE_SWITCH}_{subnet}_{dev_id}_{channel}"

            if key in self.devices:
                errors["base"] = "already_exists"
                device_key_label = self._get_device_display_name(
                    key, self.devices[key]
                )
            else:
                self.devices[key] = {
                    CONF_DEVICE_TYPE: DEVICE_TYPE_SWITCH,
                    CONF_SUBNET_ID: subnet,
                    CONF_DEVICE_ID: dev_id,
                    CONF_CHANNEL: channel,
                    "name": user_input.get("name", f"Switch {subnet}-{dev_id}-{channel}"),
                }
                return await self.async_step_init()

        return self.async_show_form(
            step_id="add_switch",
            data_schema=vol.Schema({
                vol.Required(CONF_SUBNET_ID): int,
                vol.Required(CONF_DEVICE_ID): int,
                vol.Required(CONF_CHANNEL): int,
                vol.Optional("name", default=""): str,
            }),
            errors=errors,
            description_placeholders={"device_key": device_key_label},
        )

    async def async_step_add_binary_sensor(self, user_input=None):
        """Step: add a binary sensor device."""
        errors = {}
        device_key_label = ""
        if user_input is not None:
            subnet = user_input[CONF_SUBNET_ID]
            dev_id = user_input[CONF_DEVICE_ID]
            channel = user_input[CONF_CHANNEL]
            subtype = user_input[CONF_SUBTYPE]
            key = f"{DEVICE_TYPE_BINARY_SENSOR}_{subnet}_{dev_id}_{channel}_{subtype}"

            if key in self.devices:
                errors["base"] = "already_exists"
                device_key_label = self._get_device_display_name(
                    key, self.devices[key]
                )
            else:
                self.devices[key] = {
                    CONF_DEVICE_TYPE: DEVICE_TYPE_BINARY_SENSOR,
                    CONF_SUBNET_ID: subnet,
                    CONF_DEVICE_ID: dev_id,
                    CONF_CHANNEL: channel,
                    CONF_SUBTYPE: subtype,
                    "name": user_input.get("name", f"Sensor {subnet}-{dev_id}-{channel}"),
                }
                return await self.async_step_init()

        return self.async_show_form(
            step_id="add_binary_sensor",
            data_schema=vol.Schema({
                vol.Required(CONF_SUBNET_ID): int,
                vol.Required(CONF_DEVICE_ID): int,
                vol.Required(CONF_CHANNEL): int,
                vol.Required(CONF_SUBTYPE): vol.In(BINARY_SENSOR_SUBTYPES),
                vol.Optional("name", default=""): str,
            }),
            errors=errors,
            description_placeholders={"device_key": device_key_label},
        )

    async def async_step_add_sensor(self, user_input=None):
        """Step: add a sensor device (no channel)."""
        errors = {}
        device_key_label = ""
        if user_input is not None:
            subnet = user_input[CONF_SUBNET_ID]
            dev_id = user_input[CONF_DEVICE_ID]
            subtype = user_input[CONF_SUBTYPE]
            key = f"{DEVICE_TYPE_SENSOR}_{subnet}_{dev_id}_{subtype}"

            if key in self.devices:
                errors["base"] = "already_exists"
                device_key_label = self._get_device_display_name(
                    key, self.devices[key]
                )
            else:
                self.devices[key] = {
                    CONF_DEVICE_TYPE: DEVICE_TYPE_SENSOR,
                    CONF_SUBNET_ID: subnet,
                    CONF_DEVICE_ID: dev_id,
                    CONF_SUBTYPE: subtype,
                    "name": user_input.get("name", f"Sensor {subnet}-{dev_id}"),
                }
                return await self.async_step_init()

        return self.async_show_form(
            step_id="add_sensor",
            data_schema=vol.Schema({
                vol.Required(CONF_SUBNET_ID): int,
                vol.Required(CONF_DEVICE_ID): int,
                vol.Required(CONF_SUBTYPE): vol.In(SENSOR_SUBTYPES),
                vol.Optional("name", default=""): str,
            }),
            errors=errors,
            description_placeholders={"device_key": device_key_label},
        )

    async def async_step_add_cover(self, user_input=None):
        """Step: add a cover (curtain) device."""
        errors = {}
        device_key_label = ""
        if user_input is not None:
            subnet = user_input[CONF_SUBNET_ID]
            dev_id = user_input[CONF_DEVICE_ID]
            channel = user_input[CONF_CHANNEL]
            subtype = user_input[CONF_SUBTYPE]
            key = f"{DEVICE_TYPE_COVER}_{subnet}_{dev_id}_{channel}_{subtype}"

            if key in self.devices:
                errors["base"] = "already_exists"
                device_key_label = self._get_device_display_name(
                    key, self.devices[key]
                )
            else:
                self.devices[key] = {
                    CONF_DEVICE_TYPE: DEVICE_TYPE_COVER,
                    CONF_SUBNET_ID: subnet,
                    CONF_DEVICE_ID: dev_id,
                    CONF_CHANNEL: channel,
                    CONF_SUBTYPE: subtype,
                    CONF_TRAVEL_TIME: user_input.get(CONF_TRAVEL_TIME, 15),
                    "name": user_input.get("name", f"Cover {subnet}-{dev_id}-{channel}"),
                }
                return await self.async_step_init()

        return self.async_show_form(
            step_id="add_cover",
            data_schema=vol.Schema({
                vol.Required(CONF_SUBNET_ID): int,
                vol.Required(CONF_DEVICE_ID): int,
                vol.Required(CONF_CHANNEL): int,
                vol.Required(CONF_SUBTYPE): vol.In(COVER_SUBTYPES),
                vol.Optional(CONF_TRAVEL_TIME, default=15): int,
                vol.Optional("name", default=""): str,
            }),
            errors=errors,
            description_placeholders={"device_key": device_key_label},
        )

    async def async_step_add_button(self, user_input=None):
        """Step: add a button device (momentary universal switch output)."""
        errors = {}
        device_key_label = ""
        if user_input is not None:
            subnet = user_input[CONF_SUBNET_ID]
            dev_id = user_input[CONF_DEVICE_ID]
            switch_number = user_input[CONF_CHANNEL]
            key = f"{DEVICE_TYPE_BUTTON}_{subnet}_{dev_id}_{switch_number}"

            if key in self.devices:
                errors["base"] = "already_exists"
                device_key_label = self._get_device_display_name(
                    key, self.devices[key]
                )
            else:
                self.devices[key] = {
                    CONF_DEVICE_TYPE: DEVICE_TYPE_BUTTON,
                    CONF_SUBNET_ID: subnet,
                    CONF_DEVICE_ID: dev_id,
                    CONF_CHANNEL: switch_number,
                    "name": user_input.get("name", f"Button {subnet}-{dev_id}-{switch_number}"),
                }
                return await self.async_step_init()

        return self.async_show_form(
            step_id="add_button",
            data_schema=vol.Schema({
                vol.Required(CONF_SUBNET_ID): int,
                vol.Required(CONF_DEVICE_ID): int,
                vol.Required(CONF_CHANNEL): int,
                vol.Optional("name", default=""): str,
            }),
            errors=errors,
            description_placeholders={"device_key": device_key_label},
        )

    async def async_step_add_climate(self, user_input=None):
        """Step: add a climate device (floor heating, AC module or panel AC)."""
        errors = {}
        device_key_label = ""
        if user_input is not None:
            subnet = user_input[CONF_SUBNET_ID]
            dev_id = user_input[CONF_DEVICE_ID]
            subtype = user_input[CONF_SUBTYPE]
            ac_number = user_input.get(CONF_AC_NUMBER, 1)
            # Only meaningful for the ac_panel subtype: which panel channel
            # carries the room-temperature reading (0 disables it).
            channel = user_input.get(CONF_CHANNEL, 1)
            key = f"{DEVICE_TYPE_CLIMATE}_{subnet}_{dev_id}_{subtype}_{ac_number}"

            if key in self.devices:
                errors["base"] = "already_exists"
                device_key_label = self._get_device_display_name(
                    key, self.devices[key]
                )
            else:
                self.devices[key] = {
                    CONF_DEVICE_TYPE: DEVICE_TYPE_CLIMATE,
                    CONF_SUBNET_ID: subnet,
                    CONF_DEVICE_ID: dev_id,
                    CONF_SUBTYPE: subtype,
                    CONF_AC_NUMBER: ac_number,
                    CONF_CHANNEL: channel,
                    "name": user_input.get("name", f"Climate {subnet}-{dev_id}"),
                }
                return await self.async_step_init()

        return self.async_show_form(
            step_id="add_climate",
            data_schema=vol.Schema({
                vol.Required(CONF_SUBNET_ID): int,
                vol.Required(CONF_DEVICE_ID): int,
                vol.Required(CONF_SUBTYPE): vol.In(CLIMATE_SUBTYPES),
                vol.Optional(CONF_AC_NUMBER, default=1): int,
                vol.Optional(CONF_CHANNEL, default=1): int,
                vol.Optional("name", default=""): str,
            }),
            errors=errors,
            description_placeholders={"device_key": device_key_label},
        )

    async def async_step_add_scene(self, user_input=None):
        """Step: add a scene device (area_number.scene_number on a device)."""
        errors = {}
        device_key_label = ""
        if user_input is not None:
            subnet = user_input[CONF_SUBNET_ID]
            dev_id = user_input[CONF_DEVICE_ID]
            area_number = user_input[CONF_AREA_NUMBER]
            scene_number = user_input[CONF_SCENE_NUMBER]
            key = f"{DEVICE_TYPE_SCENE}_{subnet}_{dev_id}_{area_number}_{scene_number}"

            if key in self.devices:
                errors["base"] = "already_exists"
                device_key_label = self._get_device_display_name(
                    key, self.devices[key]
                )
            else:
                self.devices[key] = {
                    CONF_DEVICE_TYPE: DEVICE_TYPE_SCENE,
                    CONF_SUBNET_ID: subnet,
                    CONF_DEVICE_ID: dev_id,
                    CONF_AREA_NUMBER: area_number,
                    CONF_SCENE_NUMBER: scene_number,
                    "name": user_input.get(
                        "name", f"Scene {subnet}-{dev_id} {area_number}.{scene_number}"
                    ),
                }
                return await self.async_step_init()

        return self.async_show_form(
            step_id="add_scene",
            data_schema=vol.Schema({
                vol.Required(CONF_SUBNET_ID): int,
                vol.Required(CONF_DEVICE_ID): int,
                vol.Required(CONF_AREA_NUMBER): int,
                vol.Required(CONF_SCENE_NUMBER): int,
                vol.Optional("name", default=""): str,
            }),
            errors=errors,
            description_placeholders={"device_key": device_key_label},
        )

    async def async_step_remove_device(self, user_input=None):
        """Step: remove a device."""
        if user_input is not None:
            device_key = user_input.get("device")
            if device_key and device_key in self.devices:
                del self.devices[device_key]
            return await self.async_step_init()

        if not self.devices:
            return self.async_show_form(
                step_id="remove_device",
                data_schema=vol.Schema({}),
                description_placeholders={"message": "No devices to remove."},
            )

        device_options = {
            key: self._get_device_display_name(key, cfg)
            for key, cfg in self.devices.items()
        }

        return self.async_show_form(
            step_id="remove_device",
            data_schema=vol.Schema({
                vol.Required("device"): vol.In(device_options),
            }),
        )
