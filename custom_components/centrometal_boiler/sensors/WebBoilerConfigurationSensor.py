from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant

from .WebBoilerGenericSensor import WebBoilerGenericSensor


class WebBoilerConfigurationSensor(WebBoilerGenericSensor):

    def _derive_state_class(self):
        """This sensor reports a textual state, so it has no state class."""
        return None

    @staticmethod
    def create_entities(hass: HomeAssistant, device) -> list[SensorEntity]:
        """Create entities."""
        entities = []
        entities.append(
            WebBoilerConfigurationSensor(
                hass,
                device,
                [None, "mdi:state-machine", None, "Configuration"],
                device.get_parameter("B_KONF"),
            )
        )
        return entities
