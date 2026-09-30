from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant

# pylint: disable=relative-beyond-top-level
from ..common import format_name, format_time
from ..entity import WebBoilerEntity


class WebBoilerPowerSwitch(WebBoilerEntity, SwitchEntity):
    """Turns the boiler on or off; its state follows B_STATE."""

    def __init__(self, hass: HomeAssistant, device) -> None:
        super().__init__(hass, device)
        self._attr_name = format_name(hass, device, f"{device['product']} Boiler Switch")
        self._attr_unique_id = device["serial"]
        self._param = device.get_parameter("B_STATE")

    def _watched(self) -> list:
        return [self._param]

    @property
    def is_on(self):
        return self._param["value"] != "OFF"

    @property
    def extra_state_attributes(self):
        attributes = {}
        if "timestamp" in self._param.keys():
            attributes["Last updated"] = format_time(self.hass, int(self._param["timestamp"]))
        return attributes

    async def async_turn_on(self, **kwargs):
        await self._async_turn(True)

    async def async_turn_off(self, **kwargs):
        await self._async_turn(False)

    async def _async_turn(self, on: bool):
        serial = self.device["serial"]
        await self.web_boiler_system.async_send_command(
            f"turn the boiler {serial} {'on' if on else 'off'}",
            lambda: self.web_boiler_client.turn(serial, on),
        )
