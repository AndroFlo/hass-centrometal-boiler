"""Support for the Centrometal BioTec-Plus boiler (CM WiFi-Box, web-boiler.com cloud)."""

import logging
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
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_PRODUCT_PREFIX,
    DOMAIN,
    SUPPORTED_DEVICE_TYPE,
    WEB_BOILER_CLIENT,
    WEB_BOILER_LOGIN_RETRY_INTERVAL,
    WEB_BOILER_REFRESH_INTERVAL,
    WEB_BOILER_SYSTEM,
    WEB_BOILER_UNSUBSCRIBE,
    connectivity_signal,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "switch", "binary_sensor", "button"]

# This integration is set up from the UI only, never from configuration.yaml.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Centrometal Boiler System integration."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    unique_id = entry.data[CONF_EMAIL]
    web_boiler_system = WebBoilerSystem(hass, entry)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][unique_id] = {WEB_BOILER_SYSTEM: web_boiler_system}

    try:
        # Raises ConfigEntryNotReady (Home Assistant retries later), ConfigEntryAuthFailed
        # (asks for new credentials) or ConfigEntryError (no supported boiler).
        await web_boiler_system.start()
    except Exception:
        await web_boiler_system.stop()
        hass.data[DOMAIN].pop(unique_id, None)
        raise

    unsubscribe = []

    async def async_stop_system(event) -> None:
        await web_boiler_system.stop()

    unsubscribe.append(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop_system))

    # Watchdog every second: reconnect when the WebSocket is down, refresh periodically.
    # The loop reschedules itself; keep the latest handle to cancel it on unload.
    tick_handle = {"cancel": None}

    def schedule_tick() -> None:
        tick_handle["cancel"] = async_call_later(hass, 1.0, fire_time_event)

    async def fire_time_event(now) -> None:
        try:
            await web_boiler_system.tick()
        finally:
            schedule_tick()

    def cancel_tick() -> None:
        if tick_handle["cancel"] is not None:
            tick_handle["cancel"]()
            tick_handle["cancel"] = None

    unsubscribe.append(cancel_tick)
    schedule_tick()

    hass.data[DOMAIN][unique_id][WEB_BOILER_UNSUBSCRIBE] = unsubscribe

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.debug("Centrometal Boiler System set up (%s)", unique_id)
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

    _LOGGER.debug("Centrometal Boiler System unloaded (%s)", unique_id)
    return True


class WebBoilerSystem:
    """The connection of one Centrometal account, owned by its config entry.

    It logs in, loads the boilers, keeps the WebSocket alive (tick), refreshes the
    values when the connection comes up, and sends the commands of the entities.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self._hass = hass
        self._entry = entry
        self.username = entry.data[CONF_EMAIL]
        self.password = entry.data[CONF_PASSWORD]
        self.prefix = entry.data.get(CONF_PREFIX, "").rstrip()
        if len(self.prefix) > 0:
            self.prefix = self.prefix + " "
        self.product_prefix = entry.data.get(CONF_PRODUCT_PREFIX, True)
        self.web_boiler_client = WebBoilerClient()
        self.web_boiler_client.set_connectivity_callback(self._on_connectivity)
        self.last_relogin_timestamp = time.time()
        self.last_refresh_timestamp = time.time()

    async def on_parameter_updated(self, device, param, create=False):
        # Boilers push hundreds of parameters continuously: keep this at debug
        # level so the Home Assistant log stays usable.
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        _LOGGER.debug(
            "%s %s %s = %s (%s)",
            "Create" if create else "update",
            device["serial"],
            param["name"],
            param["value"],
            self.username,
        )

    async def _on_connectivity(self, connected: bool) -> None:
        async_dispatcher_send(self._hass, connectivity_signal(self.username), connected)
        if connected:
            # The boilers push all their values again, now that the subscriptions exist.
            # In the background: refresh() waits a few seconds between requests.
            self.last_refresh_timestamp = time.time()
            self._entry.async_create_background_task(
                self._hass, self.web_boiler_client.refresh(), "centrometal_boiler refresh"
            )

    async def start(self):
        _LOGGER.debug("Starting Centrometal Boiler System %s", self.username)
        self._hass.data[DOMAIN][self.username][WEB_BOILER_CLIENT] = self.web_boiler_client

        try:
            logged_in = await self.web_boiler_client.login(self.username, self.password)
        except Exception as ex:
            raise ConfigEntryNotReady(f"Cannot reach the Centrometal server: {ex}") from ex
        if not logged_in:
            raise ConfigEntryAuthFailed(f"Centrometal refused the credentials of {self.username}")

        try:
            got_configuration = await self.web_boiler_client.get_configuration()
        except Exception as ex:
            raise ConfigEntryNotReady(
                f"Cannot read the configuration from the Centrometal server: {ex}"
            ) from ex
        devices = self.web_boiler_client.data.values() if got_configuration else []
        if not any(device["type"] == SUPPORTED_DEVICE_TYPE for device in devices):
            raise ConfigEntryError(f"No BioTec-Plus boiler on the account {self.username}")
        for device in devices:
            if device["type"] != SUPPORTED_DEVICE_TYPE:
                _LOGGER.warning(
                    "Boiler %s (%s) is ignored: only the BioTec-Plus is supported",
                    device["serial"],
                    device["product"],
                )

        await self.web_boiler_client.start_websocket(self.on_parameter_updated)

    async def stop(self):
        _LOGGER.debug("Stopping Centrometal Boiler System %s", self.username)
        try:
            await self.web_boiler_client.close_websocket()
        except Exception as ex:
            _LOGGER.debug("Error while closing the websocket: %s", ex)
        # Closing the websocket alone leaks the aiohttp session, which shows up
        # as "Unclosed client session" warnings when Home Assistant shuts down.
        try:
            if self.web_boiler_client.http_client is not None:
                await self.web_boiler_client.http_client.close_session()
        except Exception as ex:
            _LOGGER.debug("Error while closing the HTTP session: %s", ex)
        return True

    async def tick(self):
        """Called every second: reconnect if needed, refresh from time to time."""
        now = time.time()
        if not self.web_boiler_client.is_websocket_connected():
            if now - self.last_relogin_timestamp > WEB_BOILER_LOGIN_RETRY_INTERVAL:
                _LOGGER.info("Connection lost, logging in again (%s)", self.username)
                await self.relogin()
        elif now - self.last_refresh_timestamp > WEB_BOILER_REFRESH_INTERVAL:
            self.last_refresh_timestamp = now
            _LOGGER.debug("Periodic refresh (%s)", self.username)
            if not await self.web_boiler_client.refresh():
                await self.relogin()

    async def relogin(self):
        """Log in again and restart the WebSocket; never raises (tick retries in a minute)."""
        self.last_relogin_timestamp = time.time()
        try:
            await self.web_boiler_client.close_websocket()
            logged_in = await self.web_boiler_client.relogin()
        except Exception as ex:
            _LOGGER.warning(
                "Cannot reach the Centrometal server, retrying in %s s: %s (%s)",
                WEB_BOILER_LOGIN_RETRY_INTERVAL,
                ex,
                self.username,
            )
            return
        if not logged_in:
            # The password was changed on the Centrometal side: ask the user for it
            _LOGGER.warning("Centrometal refused the credentials (%s)", self.username)
            self._entry.async_start_reauth(self._hass)
            return
        # The values are refreshed when the connection is up (_on_connectivity)
        await self.web_boiler_client.start_websocket(self.on_parameter_updated)

    async def async_send_command(self, what: str, send) -> None:
        """Send a command, logging in again and retrying once if it is refused.

        send() returns True when the server accepted the command (the library's
        commands never raise). what describes the command for the error message.
        """
        if await send():
            return
        # The session most likely expired: log in again and retry once
        try:
            logged_in = await self.web_boiler_client.relogin()
        except Exception as ex:
            raise HomeAssistantError(f"Cannot reach the Centrometal server to {what}: {ex}") from ex
        if logged_in and await send():
            return
        raise HomeAssistantError(f"The Centrometal server refused to {what}")
