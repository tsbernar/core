"""The Home Assistant Yellow integration."""
from __future__ import annotations

from datetime import timedelta
import logging

import async_timeout

from homeassistant.components.hassio import (
    AddonError,
    AddonInfo,
    AddonManager,
    AddonState,
    HassioAPIError,
    async_get_yellow_settings,
    get_os_info,
)
from homeassistant.components.homeassistant_hardware.silabs_multiprotocol_addon import (
    get_addon_manager,
    get_zigbee_socket,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, RADIO_DEVICE, ZHA_HW_DISCOVERY_DATA
from .models import YellowData

PLATFORMS = [Platform.SWITCH]

_LOGGER = logging.getLogger(__name__)


async def _multi_pan_addon_info(
    hass: HomeAssistant, entry: ConfigEntry
) -> AddonInfo | None:
    """Return AddonInfo if the multi-PAN addon is enabled for the Yellow's radio."""
    addon_manager: AddonManager = get_addon_manager(hass)
    try:
        addon_info: AddonInfo = await addon_manager.async_get_addon_info()
    except AddonError as err:
        _LOGGER.error(err)
        raise ConfigEntryNotReady from err

    # Start the addon if it's not started
    if addon_info.state == AddonState.NOT_RUNNING:
        await addon_manager.async_start_addon()

    if addon_info.state not in (AddonState.NOT_INSTALLED, AddonState.RUNNING):
        _LOGGER.debug(
            "Multi pan addon in state %s, delaying yellow config entry setup",
            addon_info.state,
        )
        raise ConfigEntryNotReady

    if addon_info.state == AddonState.NOT_INSTALLED:
        return None

    if addon_info.options["device"] != RADIO_DEVICE:
        return None

    return addon_info


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Home Assistant Yellow config entry."""
    if (os_info := get_os_info(hass)) is None:
        # The hassio integration has not yet fetched data from the supervisor
        raise ConfigEntryNotReady

    board: str | None
    if (board := os_info.get("board")) is None or not board == "yellow":
        # Not running on a Home Assistant Yellow, Home Assistant may have been migrated
        hass.async_create_task(hass.config_entries.async_remove(entry.entry_id))
        return False

    async def async_update_data() -> dict[str, bool]:
        """Fetch data from API endpoint."""
        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with async_timeout.timeout(10):
                led_settings: dict[str, bool] = await async_get_yellow_settings(hass)
                return led_settings
        except HassioAPIError as err:
            raise UpdateFailed(
                "Failed to call supervisor endpoint /os/boards/yellow"
            ) from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        # Name of the data. For logging purposes.
        name=DOMAIN,
        update_method=async_update_data,
        # Polling interval. Will only be polled if there are subscribers.
        update_interval=timedelta(seconds=30),
    )
    await coordinator.async_config_entry_first_refresh()

    addon_info = await _multi_pan_addon_info(hass, entry)

    if not addon_info:
        hw_discovery_data = ZHA_HW_DISCOVERY_DATA
    else:
        hw_discovery_data = {
            "name": "Yellow Multi-PAN",
            "port": {
                "path": get_zigbee_socket(hass, addon_info),
            },
            "radio_type": "ezsp",
        }

    await hass.config_entries.flow.async_init(
        "zha",
        context={"source": "hardware"},
        data=hw_discovery_data,
    )

    hass.data[DOMAIN] = YellowData(coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data.pop(DOMAIN)
    return unload_ok
