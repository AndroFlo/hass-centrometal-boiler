"""Support for Centrometal Boiler System."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .common import format_name, supported_devices
from .const import DOMAIN, WEB_BOILER_CLIENT, connectivity_signal
from .entity import WebBoilerEntity


async def async_setup_entry(hass: HomeAssistant, config_entry, async_add_entities):
    entities = []

    unique_id = config_entry.data[CONF_EMAIL]
    web_boiler_client = hass.data[DOMAIN][unique_id][WEB_BOILER_CLIENT]
    for device in supported_devices(web_boiler_client):
        entities.append(WebBoilerWebsocketStatus(hass, device))
    async_add_entities(entities)


class WebBoilerWebsocketStatus(WebBoilerEntity, BinarySensorEntity):
    """Whether the real-time connection to the Centrometal cloud is up."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, hass: HomeAssistant, device) -> None:
        super().__init__(hass, device)
        self._attr_unique_id = device["serial"] + "_websocket_status"
        self._attr_name = format_name(hass, device, "Centrometal Boiler System connection")

    async def async_added_to_hass(self):
        # WebBoilerSystem sends this signal whenever the connection goes up or down
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, connectivity_signal(self.device.username), self._on_connectivity
            )
        )

    @callback
    def _on_connectivity(self, connected: bool) -> None:
        # @callback: run in the event loop, where async_write_ha_state must be called
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        # This sensor reports the connection itself, so it is always available
        return True

    @property
    def is_on(self) -> bool:
        return self.web_boiler_client.is_websocket_connected()
