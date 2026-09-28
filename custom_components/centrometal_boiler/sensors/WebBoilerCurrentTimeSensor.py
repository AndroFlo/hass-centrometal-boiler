from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import UTC

from .WebBoilerGenericSensor import WebBoilerGenericSensor
from ..common import format_time


class WebBoilerCurrentTimeSensor(WebBoilerGenericSensor):

    def _derive_state_class(self):
        """This sensor reports a textual state, so it has no state class."""
        return None

    @property
    def native_value(self):
        """Return the value of the sensor."""
        value = self.parameter["value"]
        if value == "?":
            return value
        # The clock is pushed as a hexadecimal epoch and is missing until the
        # first update arrives.
        try:
            timestamp = int(value, 16)
        except (ValueError, TypeError):
            return None
        return format_time(self.hass, timestamp, UTC)

    @staticmethod
    def create_entities(hass: HomeAssistant, device) -> list[SensorEntity]:
        entities = []
        entities.append(
            WebBoilerCurrentTimeSensor(
                hass,
                device,
                [None, "mdi:clock-outline", None, "Clock"],
                device.get_parameter("B_Time"),
            )
        )
        return entities
