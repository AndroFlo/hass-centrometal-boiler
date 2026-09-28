from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

# pylint: disable=relative-beyond-top-level
from ..const import DOMAIN, WEB_BOILER_CLIENT
from ..common import create_device_info, format_name, format_time

from homeassistant.components.switch import SwitchEntity


class WebBoilerCircuitSwitch(SwitchEntity):
    """Representation of a boiler Power Switch."""

    def __init__(self, hass: HomeAssistant, device, naslov, dbindex) -> None:
        """Initialize the Boiler Power Switch."""
        self.hass = hass
        self.web_boiler_client = hass.data[DOMAIN][device.username][WEB_BOILER_CLIENT]
        self._device = device
        self._product = device["product"]
        self._serial = device["serial"]
        self._name = format_name(hass, device, naslov)
        self._unique_id = device["serial"] + "_switch_" + str(dbindex)
        self._dbindex = dbindex
        self._table_key = f"table_{dbindex}_switch"
        self._param_name_def = f"PDEF_{dbindex}_0"
        self._param_name_state = f"PVAL_{dbindex}_0"
        self._param_name_off = f"PMIN_{dbindex}_0"
        self._param_name_on = f"PMAX_{dbindex}_0"
        self._param_def = self._device.get_parameter(self._param_name_def)
        self._param_state = self._device.get_parameter(self._param_name_state)
        self._param_off = self._device.get_parameter(self._param_name_off)
        self._param_on = self._device.get_parameter(self._param_name_on)
        self._param_def["used"] = True
        self._param_state["used"] = True
        self._param_off["used"] = True
        self._param_on["used"] = True

    async def async_added_to_hass(self):
        """Subscribe to events."""
        self.async_schedule_update_ha_state(False)
        self._set_callback(self.update_callback)

    async def async_will_remove_from_hass(self):
        """Unsubscribe when the entity is removed."""
        self._set_callback(None)

    def _set_callback(self, callback):
        for param in (
            self._param_def,
            self._param_state,
            self._param_off,
            self._param_on,
        ):
            param.set_update_callback(callback, self._table_key)

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
        try:
            return int(self._param_state["value"]) == int(self._param_on["value"])
        except (ValueError, TypeError, KeyError):
            return False

    @property
    def available(self):
        """Return True if the device is available."""
        return self.web_boiler_client.is_websocket_connected()

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the circuit switch."""
        last_updated = "?"
        if "timestamp" in self._param_state.keys():
            last_updated = format_time(
                self.hass, int(self._param_state["timestamp"])
            )
        return {"Last updated": last_updated}

    async def async_turn_on(self, **kwargs):
        await self._async_turn_circuit(True)

    async def async_turn_off(self, **kwargs):
        await self._async_turn_circuit(False)

    async def _async_turn_circuit(self, value):
        """Send the command, retrying once after a re-login."""
        try:
            succeeded = await self.web_boiler_client.turn_circuit(
                self._device["serial"], self._dbindex, value
            )
        except Exception as ex:
            raise HomeAssistantError(
                f"Failed to switch circuit {self._dbindex}: {ex}"
            ) from ex

        if succeeded:
            return

        # The session most likely expired: re-login and try once more.
        await self.web_boiler_client.relogin()
        try:
            succeeded = await self.web_boiler_client.turn_circuit(
                self._device["serial"], self._dbindex, value
            )
        except Exception as ex:
            raise HomeAssistantError(
                f"Failed to switch circuit {self._dbindex} after re-login: {ex}"
            ) from ex

        if not succeeded:
            raise HomeAssistantError(
                f"The Centrometal server refused to switch circuit {self._dbindex}"
            )

    @property
    def device_info(self):
        return create_device_info(self._device)
