"""Support for Centrometa Boiler devices."""

import logging
import datetime
import time

from centrometal_web_boiler import WebBoilerClient

from homeassistant.config_entries import ConfigEntry

from homeassistant.const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_PREFIX,
    EVENT_HOMEASSISTANT_STOP,
)

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_PRODUCT_PREFIX,
    DOMAIN,
    WEB_BOILER_CLIENT,
    WEB_BOILER_SYSTEM,
    WEB_BOILER_LOGIN_RETRY_INTERVAL,
    WEB_BOILER_REFRESH_INTERVAL,
    WEB_BOILER_UNSUBSCRIBE,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "switch", "binary_sensor"]

# pylint: disable=missing-function-docstring
# pylint: disable=broad-except


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Centrometal Boiler System integration."""

    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    _LOGGER.debug("Setting up Centrometal Boiler System component")

    prefix = entry.data.get(CONF_PREFIX, "")
    product_prefix = entry.data.get(CONF_PRODUCT_PREFIX, True)
    web_boiler_system = WebBoilerSystem(
        hass,
        username=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
        prefix=prefix,
        product_prefix=product_prefix,
    )

    unique_id = entry.data[CONF_EMAIL]
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][unique_id] = {}
    hass.data[DOMAIN][unique_id][WEB_BOILER_SYSTEM] = web_boiler_system

    if not await web_boiler_system.start():
        # Leave no half-initialized state behind: Home Assistant will retry the
        # setup (or ask for new credentials) instead of showing an entry with
        # zero entities.
        await web_boiler_system.stop()
        hass.data[DOMAIN].pop(unique_id, None)
        raise ConfigEntryAuthFailed(
            f"Cannot log in to the Centrometal web boiler server as {unique_id}"
        )

    unsubscribe = []

    async def async_stop_system(event) -> None:
        await web_boiler_system.stop()

    unsubscribe.append(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop_system)
    )

    # The tick loop reschedules itself, so keep the latest handle around to be
    # able to cancel it in async_unload_entry.
    tick_handle = {"cancel": None}

    def schedule_tick() -> None:
        tick_handle["cancel"] = async_call_later(hass, 1.0, fire_time_event)

    async def fire_time_event(target) -> None:
        await web_boiler_system.tick()
        schedule_tick()

    def cancel_tick() -> None:
        if tick_handle["cancel"] is not None:
            tick_handle["cancel"]()
            tick_handle["cancel"] = None

    unsubscribe.append(cancel_tick)
    schedule_tick()

    hass.data[DOMAIN][unique_id][WEB_BOILER_UNSUBSCRIBE] = unsubscribe

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug(
        "Centrometal Boiler System component setup finished "
        + web_boiler_system.username
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Centrometal Boiler System config entry."""
    unique_id = entry.data[CONF_EMAIL]

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    entry_data = hass.data[DOMAIN].pop(unique_id, {})

    for unsubscribe in entry_data.get(WEB_BOILER_UNSUBSCRIBE, []):
        unsubscribe()

    web_boiler_system = entry_data.get(WEB_BOILER_SYSTEM)
    if web_boiler_system is not None:
        await web_boiler_system.stop()

    _LOGGER.debug("Centrometal Boiler System component unloaded %s", unique_id)
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


class WebBoilerSystem:
    """A Centrometal Boiler System class."""

    def __init__(self, hass, *, username, password, prefix, product_prefix):
        """Initialize the Centrometal Boiler System."""
        self._hass = hass
        self.username = username
        self.password = password
        self.prefix = prefix.rstrip()
        if len(self.prefix) > 0:
            self.prefix = self.prefix + " "
        self.product_prefix = product_prefix
        self.web_boiler_client = WebBoilerClient()
        self.last_relogin_timestamp = datetime.datetime.timestamp(
            datetime.datetime.now()
        )
        self.last_refresh_timestamp = datetime.datetime.timestamp(
            datetime.datetime.now()
        )

    async def on_parameter_updated(self, device, param, create=False):
        # Boilers push hundreds of parameters continuously: keep this at debug
        # level so the Home Assistant log stays usable.
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        action = "Create" if create else "update"
        _LOGGER.debug(
            "%s %s %s = %s (%s)",
            action,
            device["serial"],
            param["name"],
            param["value"],
            self.web_boiler_client.username,
        )

    async def start(self):
        _LOGGER.debug(f"Starting Centrometal Boiler System {self.username}")
        self._hass.data[DOMAIN][self.username][WEB_BOILER_CLIENT] = (
            self.web_boiler_client
        )

        try:
            loggedIn = await self.web_boiler_client.login(self.username, self.password)
            if not loggedIn:
                raise Exception(
                    f"Cannot login to Centrometal web boiler server {self.username}"
                )
            gotConfiguration = await self.web_boiler_client.get_configuration()
            if not gotConfiguration:
                raise Exception(
                    f"Cannot get configuration from Centrometal server {self.username}"
                )
            if len(self.web_boiler_client.data) == 0:
                raise Exception(
                    f"No device found to Centrometal web boiler server {self.username}"
                )
            await self.web_boiler_client.start_websocket(self.on_parameter_updated)
            await self.web_boiler_client.refresh()
            return True
        except Exception as ex:
            _LOGGER.error("Authentication failed : %s", str(ex))
            return False

    async def stop(self):
        _LOGGER.debug(
            f"Stopping Centrometal WebBoilerSystem {self.web_boiler_client.username}"
        )
        try:
            await self.web_boiler_client.close_websocket()
        except Exception as ex:
            _LOGGER.debug("Error while closing the websocket: %s", ex)
        # Closing the websocket alone leaks the aiohttp session, which shows up
        # as "Unclosed client session" warnings when Home Assistant shuts down.
        try:
            await self.web_boiler_client.http_client.close_session()
        except Exception as ex:
            _LOGGER.debug("Error while closing the HTTP session: %s", ex)
        return True

    async def tick(self):
        datenow = datetime.datetime.now()
        timestamp = datetime.datetime.timestamp(datenow)
        if not self.web_boiler_client.is_websocket_connected():
            if (
                timestamp - self.last_relogin_timestamp
                > WEB_BOILER_LOGIN_RETRY_INTERVAL
            ):
                _LOGGER.info(
                    f"Centrometal WebBoilerSystem::tick trying to relogin {self.web_boiler_client.username}"
                )
                await self.relogin()
        else:
            if timestamp - self.last_refresh_timestamp > WEB_BOILER_REFRESH_INTERVAL:
                self.last_refresh_timestamp = timestamp
                _LOGGER.info(
                    f"WebBoilerSystem::tick refresh data {self.web_boiler_client.username}"
                )
                refresh_successful = await self.web_boiler_client.refresh()
                if not refresh_successful:
                    await self.relogin()

    async def relogin(self):
        self.last_relogin_timestamp = time.time()
        await self.web_boiler_client.close_websocket()
        await self.web_boiler_client.http_client.close_session()
        relogin_successful = await self.web_boiler_client.relogin()
        if relogin_successful:
            await self.web_boiler_client.start_websocket(self.on_parameter_updated)
            await self.web_boiler_client.refresh()
        else:
            _LOGGER.warning(
                f"WebBoilerSystem::tick failed to relogin {self.web_boiler_client.username}"
            )
