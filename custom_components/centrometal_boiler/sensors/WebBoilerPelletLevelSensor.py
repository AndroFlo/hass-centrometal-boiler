from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant

from .WebBoilerGenericSensor import WebBoilerGenericSensor


class WebBoilerPelletLevelSensor(WebBoilerGenericSensor):

    def _derive_state_class(self):
        """This sensor reports a textual state, so it has no state class."""
        return None

    @property
    def native_value(self):
        """Return the value of the sensor."""
        configurations = ["Empty", "Reserve", "Full"]
        try:
            return configurations[int(self.parameter["value"])]
        except (ValueError, TypeError, IndexError, KeyError):
            pass
        return self.parameter["value"]

    @staticmethod
    def create_entities(hass: HomeAssistant, device) -> list[SensorEntity]:
        entities = []
        entities.append(
            WebBoilerPelletLevelSensor(
                hass,
                device,
                [None, "mdi:bucket-outline", None, "Tank Level"],
                device.get_parameter("B_razina"),
            )
        )
        return entities
