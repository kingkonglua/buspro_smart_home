"""Minimal subset of ``homeassistant.exceptions`` used by buspro.

Real Home Assistant defines ``ConfigEntryNotReady`` here (deriving from
``HomeAssistantError``); ``async_setup_entry`` imports it so HA backs the entry
off and retries instead of marking it a permanent ``SETUP_ERROR``.

This stub exists only so the offline test/dry-run harness can import and raise
the real symbol from the real import path.
"""


class HomeAssistantError(Exception):
    """Base class for Home Assistant errors."""


class ConfigEntryError(HomeAssistantError):
    """A config entry encountered a permanent error while setting up."""


class ConfigEntryNotReady(HomeAssistantError):
    """A config entry is not ready yet; Home Assistant will retry with backoff."""


class ConfigEntryAuthFailed(ConfigEntryError):
    """Authentication for a config entry failed."""
