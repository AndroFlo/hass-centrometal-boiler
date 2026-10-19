"""Sensors telling when the wood fire must be lit again.

Unlike every other sensor of this integration, these do not display a boiler
parameter: they publish what BufferPredictionCalculator computed. They share its
result, so the three of them can never disagree.
"""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant, callback

from ..common import format_name
from ..entity import WebBoilerEntity
from ..prediction import PARAM_BUFFER_TOP


class WebBoilerPredictionSensor(WebBoilerEntity, SensorEntity):
    """Base of the prediction sensors: subscribes to the calculator."""

    def __init__(self, hass: HomeAssistant, device, calculator, description: str, key: str) -> None:
        super().__init__(hass, device)
        self.calculator = calculator
        product = device["product"]
        if self.web_boiler_system.product_prefix:
            self._attr_name = format_name(hass, device, f"{product} {description}")
        else:
            self._attr_name = format_name(hass, device, description)
        self._attr_unique_id = f"{device['serial']}-{key}"
        # The tank probe keeps the availability in step with the connection, the
        # way every other entity of this integration does.
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
    def extra_state_attributes(self):
        return self.calculator.result.attributes()


class WebBoilerRelightTimeSensor(WebBoilerPredictionSensor):
    """When to light the fire so the tank never runs dry."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    # Home Assistant rejects a state class on a timestamp.
    _attr_state_class = None
    _attr_icon = "mdi:fire-alert"

    @property
    def native_value(self):
        return self.calculator.result.relight_time


class WebBoilerBufferAutonomySensor(WebBoilerPredictionSensor):
    """How long the tank alone can still heat the house."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_icon = "mdi:timer-sand"

    @property
    def native_value(self):
        return self.calculator.result.autonomy_hours


class WebBoilerBufferEnergySensor(WebBoilerPredictionSensor):
    """Usable energy left in the tank.

    With a measurement state class this feeds the long-term statistics, which gives
    a discharge curve over weeks: the easiest way to tune the loss coefficient.
    """

    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-charging-70"

    @property
    def native_value(self):
        energy = self.calculator.result.stored_energy_kwh
        return None if energy is None else round(energy, 2)


def create_entities(hass: HomeAssistant, device, calculator) -> list[SensorEntity]:
    return [
        WebBoilerRelightTimeSensor(
            hass, device, calculator, "Buffer Tank Relight Time", "buffer_relight_time"
        ),
        WebBoilerBufferAutonomySensor(
            hass, device, calculator, "Buffer Tank Autonomy", "buffer_autonomy"
        ),
        WebBoilerBufferEnergySensor(
            hass, device, calculator, "Buffer Tank Stored Energy", "buffer_stored_energy"
        ),
    ]
