from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

# pylint: disable=relative-beyond-top-level
from ..common import create_device_info, format_name
from ..const import DOMAIN, WEB_BOILER_CLIENT

from homeassistant.components.button import ButtonEntity


class WebBoilerPelletModeButton(ButtonEntity):
    """Button switching a wood/pellet boiler to pellet mode."""

    def __init__(self, hass: HomeAssistant, device) -> None:
        """Initialize the pellet mode button."""
        self.hass = hass
        self.web_boiler_client = hass.data[DOMAIN][device.username][WEB_BOILER_CLIENT]
        self._device = device
        self._product = device["product"]
        self._name = format_name(hass, device, f"{self._product} Pellet Mode")
        self._unique_id = device["serial"] + "_button_pellet_mode"

    @property
    def should_poll(self) -> bool:
        """A button has no state to poll."""
        return False

    @property
    def name(self):
        """Return the name of the button."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._unique_id

    @property
    def icon(self):
        return "mdi:grain"

    @property
    def available(self):
        """Return True if the device is available."""
        return self.web_boiler_client.is_websocket_connected()

    async def async_press(self) -> None:
        """Send the command, retrying once after a re-login."""
        serial = self._device["serial"]
        if await self.web_boiler_client.set_pellet_mode(serial):
            return

        # The session most likely expired: re-login and try once more.
        await self.web_boiler_client.relogin()
        if not await self.web_boiler_client.set_pellet_mode(serial):
            raise HomeAssistantError(
                f"Failed to switch the boiler {serial} to pellet mode"
            )

    @property
    def device_info(self):
        return create_device_info(self._device)
