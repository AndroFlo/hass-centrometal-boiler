from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant

# pylint: disable=relative-beyond-top-level
from ..common import format_name, format_time
from ..entity import WebBoilerEntity


class WebBoilerCircuitSwitch(WebBoilerEntity, SwitchEntity):
    """Turns one heating circuit on or off.

    The circuit is a setting of the boiler (dbindex): PVAL is its value, PMIN and
    PMAX the values meaning off and on, PDEF the default.
    """

    def __init__(self, hass: HomeAssistant, device, naslov, dbindex) -> None:
        super().__init__(hass, device)
        self._attr_name = format_name(hass, device, naslov)
        self._attr_unique_id = f"{device['serial']}_switch_{dbindex}"
        self._dbindex = dbindex
        self._param_def = device.get_parameter(f"PDEF_{dbindex}_0")
        self._param_state = device.get_parameter(f"PVAL_{dbindex}_0")
        self._param_off = device.get_parameter(f"PMIN_{dbindex}_0")
        self._param_on = device.get_parameter(f"PMAX_{dbindex}_0")
        for param in self._watched():
            param["used"] = True

    def _watched(self) -> list:
        return [self._param_def, self._param_state, self._param_off, self._param_on]

    @property
    def is_on(self):
        try:
            return int(self._param_state["value"]) == int(self._param_on["value"])
        except (ValueError, TypeError, KeyError):
            return False

    @property
    def extra_state_attributes(self):
        last_updated = "?"
        if "timestamp" in self._param_state.keys():
            last_updated = format_time(self.hass, int(self._param_state["timestamp"]))
        return {"Last updated": last_updated}

    async def async_turn_on(self, **kwargs):
        await self._async_turn_circuit(True)

    async def async_turn_off(self, **kwargs):
        await self._async_turn_circuit(False)

    async def _async_turn_circuit(self, on: bool):
        serial = self.device["serial"]
        await self.web_boiler_system.async_send_command(
            f"turn circuit {self._dbindex} {'on' if on else 'off'}",
            lambda: self.web_boiler_client.turn_circuit(serial, self._dbindex, on),
        )
