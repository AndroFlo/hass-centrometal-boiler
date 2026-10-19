"""Tells when the wood fire has to be lit, for notifications."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback

# pylint: disable=relative-beyond-top-level
from ..common import format_name
from ..entity import WebBoilerEntity
from ..prediction import PARAM_BUFFER_TOP


class WebBoilerRelightAlert(WebBoilerEntity, BinarySensorEntity):
    """On when the buffer tank needs the fire lit again.

    Meant to drive an automation: the ignition delay and the safety margin are
    already deducted, so when this turns on it is time to go and light the stove.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:fire-alert"

    def __init__(self, hass: HomeAssistant, device, calculator) -> None:
        super().__init__(hass, device)
        self.calculator = calculator
        product = device["product"]
        description = "Buffer Tank Relight Alert"
        if self.web_boiler_system.product_prefix:
            self._attr_name = format_name(hass, device, f"{product} {description}")
        else:
            self._attr_name = format_name(hass, device, description)
        self._attr_unique_id = f"{device['serial']}-buffer_relight_alert"
        self._param = device.get_parameter(PARAM_BUFFER_TOP)

    def _watched(self) -> list:
        return [self._param]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.calculator.async_add_listener(self._on_prediction))

    @callback
    def _on_prediction(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self.calculator.result.is_alert

    @property
    def extra_state_attributes(self):
        return self.calculator.result.attributes()
