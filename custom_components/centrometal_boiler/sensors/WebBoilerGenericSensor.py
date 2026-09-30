import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant

from ..common import format_name, format_time
from ..entity import WebBoilerEntity

from .generic_sensors_all import (
    GENERIC_SENSORS_COMMON,
    get_generic_temperature_settings_sensors,
)
from .generic_sensors_biotec_plus import BIOTEC_PLUS_GENERIC_SENSORS

_LOGGER = logging.getLogger(__name__)


class WebBoilerGenericSensor(WebBoilerEntity, SensorEntity):
    """A boiler value shown as a sensor, described by a table entry.

    sensor_data = [unit, icon, device_class, description, attributes?], where
    attributes maps other parameter codes to the labels of extra state attributes.
    """

    def __init__(self, hass: HomeAssistant, device, sensor_data, parameter, disabled_by_default=False) -> None:
        super().__init__(hass, device)
        self.parameter = parameter
        self._attr_native_unit_of_measurement = sensor_data[0]
        self._attr_icon = sensor_data[1]
        self._attr_device_class = sensor_data[2]
        self._description = sensor_data[3]
        self._attributes = sensor_data[4] if len(sensor_data) == 5 else {}
        self._parameter_name = parameter["name"]
        product = device["product"]
        if self.web_boiler_system.product_prefix:
            self._attr_name = format_name(hass, device, f"{product} {self._description}")
        else:
            self._attr_name = format_name(hass, device, self._description)
        self._attr_unique_id = f"{device['serial']}-{self._parameter_name}"
        self._attr_state_class = self._derive_state_class()
        if disabled_by_default:
            self._attr_entity_registry_enabled_default = False
            self._attr_entity_registry_visible_default = False
        # Consumed parameters are not turned into "{?} CODE" discovery sensors
        self.parameter["used"] = True
        for attribute in self._attributes:
            self.device.get_parameter(attribute)["used"] = True

    def _watched(self) -> list:
        return [self.parameter]

    def _derive_state_class(self):
        """Give numeric sensors a state class so they feed long-term statistics.

        Counters (CNT_*) only ever grow and are reset when the boiler is
        serviced, which is exactly TOTAL_INCREASING. Other numeric readings are
        instantaneous measurements.
        """
        if self._attr_device_class in (
            SensorDeviceClass.ENUM,
            SensorDeviceClass.DATE,
            SensorDeviceClass.TIMESTAMP,
        ):
            return None
        if self._parameter_name.startswith("CNT_"):
            return SensorStateClass.TOTAL_INCREASING
        if self._attr_device_class is not None or self._attr_native_unit_of_measurement:
            return SensorStateClass.MEASUREMENT
        return None

    @property
    def native_value(self):
        """Return the value of the sensor."""
        value = self.parameter["value"]
        # The boiler reports "?" until a parameter has actually been received.
        # Home Assistant refuses a non-numeric state on entities that declare a
        # unit, a device class or a state class (the CNT_* counters have only
        # the latter): it would not even add the entity.
        if value == "?" and (
            self._attr_native_unit_of_measurement
            or self._attr_device_class
            or self._attr_state_class
        ):
            return None
        return value

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the sensor."""
        attributes = {}
        if "timestamp" in self.parameter:
            last_updated = format_time(self.hass, int(self.parameter["timestamp"]))
            for key, description in self._attributes.items():
                parameter = self.device.get_parameter(key)
                attributes[description] = parameter["value"] or "?"
            attributes["Last updated"] = last_updated
            attributes["Original name"] = self.parameter["name"]
        return attributes

    @staticmethod
    def create_common_entities(hass: HomeAssistant, device) -> list[SensorEntity]:
        entities = []
        for param_id, sensor_data in GENERIC_SENSORS_COMMON.items():
            parameter = device.get_parameter(param_id)
            entities.append(
                WebBoilerGenericSensor(hass, device, sensor_data, parameter)
            )
        return entities

    @staticmethod
    def create_temperatures_entities(hass: HomeAssistant, device) -> list[SensorEntity]:
        entities = []
        for param_id, sensor_data in get_generic_temperature_settings_sensors(
            device
        ).items():
            parameter = device.get_parameter(param_id)
            entities.append(
                WebBoilerGenericSensor(hass, device, sensor_data, parameter)
            )
        return entities

    @staticmethod
    def create_conf_entities(hass: HomeAssistant, device) -> list[SensorEntity]:
        entities = []
        for param_id, sensor_data in BIOTEC_PLUS_GENERIC_SENSORS.items():
            parameter = device.get_parameter(param_id)
            entities.append(
                WebBoilerGenericSensor(hass, device, sensor_data, parameter)
            )
        return entities

    @staticmethod
    def create_unknown_entities(hass: HomeAssistant, device) -> list[SensorEntity]:
        entities = []
        for param_key, param in device["parameters"].items():
            if "used" in param.keys():
                continue
            _LOGGER.info("Creating unknown entry for %s", param_key)
            sensor_data = [None, "mdi:help", None, "{?} " + param_key, {}]
            entities.append(WebBoilerGenericSensor(hass, device, sensor_data, param, True))
        return entities
