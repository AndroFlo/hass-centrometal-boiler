"""Base class shared by every entity of the integration."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

from .common import create_device_info
from .const import DOMAIN, WEB_BOILER_CLIENT, WEB_BOILER_SYSTEM


class WebBoilerEntity(Entity):
    """An entity of one boiler, updated by push (never polled).

    Subclasses list in _watched() the boiler parameters they display: the entity
    subscribes to them while it is in Home Assistant and writes its state on every
    update. When the WebSocket connects or disconnects, the library notifies every
    parameter, which also refreshes the availability.
    """

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, device) -> None:
        self.hass = hass
        self.device = device
        entry_data = hass.data[DOMAIN][device.username]
        self.web_boiler_client = entry_data[WEB_BOILER_CLIENT]
        self.web_boiler_system = entry_data[WEB_BOILER_SYSTEM]
        self._watching: list = []

    def _watched(self) -> list:
        """Parameters whose updates refresh this entity."""
        return []

    @property
    def _callback_tag(self) -> str:
        # One tag per entity: a shared tag would let two entities watching the
        # same parameter overwrite each other's callback.
        return f"entity_{self.unique_id}"

    async def async_added_to_hass(self) -> None:
        self._watching = self._watched()
        for parameter in self._watching:
            parameter.set_update_callback(self.update_callback, self._callback_tag)

    async def async_will_remove_from_hass(self) -> None:
        # Not in __del__: the callback stored on the parameter keeps the entity
        # alive, so the garbage collector would never call it.
        for parameter in self._watching:
            parameter.set_update_callback(None, self._callback_tag)
        self._watching = []

    async def update_callback(self, parameter) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self.web_boiler_client.is_websocket_connected()

    @property
    def device_info(self):
        return create_device_info(self.device)
