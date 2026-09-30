DOMAIN = "buspro"

# Gateway configuration
CONF_HOST = "host"
CONF_PORT = "port"
DEFAULT_PORT = 6000

# Gateway auto-discovery (config flow step "user")
CONF_GATEWAY_CHOICE = "gateway_choice"
CHOICE_MANUAL = "manual"
CHOICE_RESCAN = "rescan"
GATEWAY_DISCOVERY_TIMEOUT = 5.0

# Bus scan (options flow)
CONF_SCAN_DURATION = "scan_duration"
DEFAULT_SCAN_DURATION = 15
MIN_SCAN_DURATION = 5
MAX_SCAN_DURATION = 60
SCAN_DEVICES_SELECTION = "devices"

# Device configuration keys
CONF_DEVICES = "devices"
CONF_DEVICE_TYPE = "device_type"
CONF_SUBNET_ID = "subnet_id"
CONF_DEVICE_ID = "device_id"
CONF_CHANNEL = "channel"
CONF_SUBTYPE = "subtype"
CONF_AC_NUMBER = "ac_number"
CONF_TRAVEL_TIME = "travel_time"
# Scene platform: HDL scene = (area_number, scene_number) triggered at a device.
CONF_AREA_NUMBER = "area_number"
CONF_SCENE_NUMBER = "scene_number"

# Device types
DEVICE_TYPE_LIGHT = "light"
DEVICE_TYPE_SWITCH = "switch"
DEVICE_TYPE_BINARY_SENSOR = "binary_sensor"
DEVICE_TYPE_SENSOR = "sensor"
DEVICE_TYPE_CLIMATE = "climate"
DEVICE_TYPE_COVER = "cover"
DEVICE_TYPE_BUTTON = "button"
DEVICE_TYPE_SCENE = "scene"

DEVICE_TYPES = [
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_SWITCH,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_SENSOR,
    DEVICE_TYPE_CLIMATE,
    DEVICE_TYPE_COVER,
    DEVICE_TYPE_BUTTON,
    DEVICE_TYPE_SCENE,
]

# Binary sensor subtypes
BINARY_SENSOR_SUBTYPES = [
    "motion",
    "dry_contact_1",
    "dry_contact_2",
    "universal_switch",
    "single_channel",
    "dry_contact",
]

# Sensor subtypes
SENSOR_SUBTYPES = [
    "illuminance",
    "temperature",
]

# Cover subtypes
COVER_SUBTYPES = [
    "curtain_module",
    "bus_motor",
]

# Climate subtypes
CLIMATE_SUBTYPES = [
    "floor_heating",
    "ac",
    # 触控面板（Enviro / Granite）空调页，见 pybuspro/devices/panel_ac.py
    "ac_panel",
]

# Scan classification types (discovery.py). These are *not* platform device
# types: "ac" imports as climate/subtype=ac, "curtain" imports as
# cover/subtype=curtain_module.
DEVICE_TYPE_AC = "ac"
DEVICE_TYPE_CURTAIN = "curtain"
