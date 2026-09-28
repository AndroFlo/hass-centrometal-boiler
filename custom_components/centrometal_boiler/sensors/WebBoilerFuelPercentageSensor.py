from homeassistant.components.sensor import SensorEntity
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
import logging

from centrometal_web_boiler.WebBoilerDeviceCollection import WebBoilerParameter

from .WebBoilerGenericSensor import WebBoilerGenericSensor

_LOGGER = logging.getLogger(__name__)


class WebBoilerFuelPercentageSensor(WebBoilerGenericSensor):
    """Fuel percentage sensor that handles late arrival of B_razP parameter."""

    async def async_added_to_hass(self):
        """Subscribe to events, switching to the real parameter if it arrived."""
        if self.device.has_parameter("B_razP"):
            real_parameter = self.device.get_parameter("B_razP")
            if real_parameter is not self.parameter:
                self.parameter = real_parameter
                self.parameter["used"] = True
                _LOGGER.debug(
                    "WebBoilerFuelPercentageSensor connected to real B_razP parameter"
                )

        await super().async_added_to_hass()

    @property
    def native_value(self):
        """Return the percentage value."""
        try:
            return int(self.parameter["value"])
        except (ValueError, TypeError, KeyError):
            return None

    @staticmethod
    def create_entities(hass: HomeAssistant, device) -> list[SensorEntity]:
        entities = []
        if device.has_parameter("B_razP"):
            param = device.get_parameter("B_razP")
        else:
            # B_razP can show up after the entities are created. Use a real
            # WebBoilerParameter as a placeholder: a plain dict would blow up in
            # async_added_to_hass, which calls set_update_callback on it.
            param = WebBoilerParameter()
            param["name"] = "B_razP"
            param["value"] = None

        entities.append(
            WebBoilerFuelPercentageSensor(
                hass,
                device,
                [
                    PERCENTAGE,
                    "mdi:percent",
                    None,
                    "Fuel level",
                ],
                param,
            )
        )
        return entities
