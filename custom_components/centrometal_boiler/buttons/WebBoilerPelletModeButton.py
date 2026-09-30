from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant

# pylint: disable=relative-beyond-top-level
from ..common import format_name
from ..entity import WebBoilerEntity


class WebBoilerPelletModeButton(WebBoilerEntity, ButtonEntity):
    """Switches the boiler from wood to pellets (there is no remote command back to wood)."""

    _attr_icon = "mdi:grain"

    def __init__(self, hass: HomeAssistant, device) -> None:
        super().__init__(hass, device)
        self._attr_name = format_name(hass, device, f"{device['product']} Pellet Mode")
        self._attr_unique_id = device["serial"] + "_button_pellet_mode"
        self._param = device.get_parameter("B_pbs")

    def _watched(self) -> list:
        # A button has no value of its own, but its availability follows the
        # connection, notified through every parameter. Without a subscription a
        # button created before the connection would stay unavailable.
        return [self._param]

    async def async_press(self) -> None:
        serial = self.device["serial"]
        await self.web_boiler_system.async_send_command(
            f"switch the boiler {serial} to pellet mode",
            lambda: self.web_boiler_client.set_pellet_mode(serial),
        )
