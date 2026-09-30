"""Subset of homeassistant.helpers.config_validation used by buspro."""

import voluptuous as vol


def positive_int(value):
    """Mirror HA's ``vol.All(vol.Coerce(int), vol.Range(min=0))``.

    Real HA raises ``vol.Invalid`` for non-coercible input; a bare ``int()``
    would leak ``TypeError`` and make schema validation untestable.
    """
    try:
        value = int(value)
    except (TypeError, ValueError) as err:
        raise vol.Invalid("expected a positive integer") from err
    if value < 0:
        raise vol.Invalid("value must be at least 0")
    return value


def string(value):
    return str(value)


def boolean(value):
    return bool(value)
