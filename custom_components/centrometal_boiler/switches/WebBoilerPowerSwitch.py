from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

# pylint: disable=relative-beyond-top-level
from ..common import create_device_info, format_name, format_time
from ..const import DOMAIN, WEB_BOILER_CLIENT, WEB_BOILER_SYSTEM

from homeassistant.components.switch import SwitchEntity


class WebBoilerPowerSwitch(SwitchEntity):
    """Representation of a boiler Power Switch."""

    def __init__(self, hass: HomeAssistant, device) -> None:
        """Initialize the Boiler Power Switch."""
        self.hass = hass
        self.web_boiler_client = hass.data[DOMAIN][device.username][WEB_BOILER_CLIENT]
        self.web_boiler_system = hass.data[DOMAIN][device.username][WEB_BOILER_SYSTEM]
        self._device = device
        self._product = device["product"]
        self._name = format_name(hass, device, f"{self._product} Boiler Switch")
        self._unique_id = device["serial"]
        self._param = device.get_parameter("B_STATE")

    async def async_added_to_hass(self):
        """Subscribe to events."""
        self.async_schedule_update_ha_state(False)
        self._param.set_update_callback(self.update_callback, "switch")

    async def async_will_remove_from_hass(self):
        """Unsubscribe when the entity is removed.

        __del__ is never reached here: the callback stored on the parameter
        keeps a reference to this entity alive.
        """
        self._param.set_update_callback(None, "switch")

    @property
    def should_poll(self) -> bool:
        """No polling needed for a power socket."""
        return False

    async def update_callback(self, device):
        """Call update for Home Assistant when the device is updated."""
        self.async_write_ha_state()

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._unique_id

    @property
    def is_on(self):
        """Return true if it is on."""
        return self._param["value"] != "OFF"

    @property
    def available(self):
        """Return True if the device is available."""
        return self.web_boiler_client.is_websocket_connected()

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the power switch."""
        attributes = {}
        if "timestamp" in self._param.keys():
            attributes["Last updated"] = format_time(
                self.hass, int(self._param["timestamp"])
            )
        return attributes

    async def async_turn_on(self, **kwargs):
        await self._async_turn(True)

    async def async_turn_off(self, **kwargs):
        await self._async_turn(False)

    async def _async_turn(self, value):
        """Send the command and surface failures instead of swallowing them."""
        try:
            result = await self.web_boiler_client.turn(self._device["serial"], value)
        except Exception as ex:
            raise HomeAssistantError(
                f"Failed to switch the boiler {self._device['serial']}: {ex}"
            ) from ex
        if result is False:
            raise HomeAssistantError(
                f"The Centrometal server refused to switch the boiler "
                f"{self._device['serial']}"
            )

    @property
    def device_info(self):
        return create_device_info(self._device)
