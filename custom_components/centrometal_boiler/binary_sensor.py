"""Support for Centrometal Boiler System."""

from homeassistant.core import HomeAssistant
from .common import create_device_info, format_name
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from homeassistant.const import (
    CONF_EMAIL,
)

from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)

from .const import DOMAIN, WEB_BOILER_CLIENT, WEB_BOILER_CONNECTIVITY


async def async_setup_entry(hass: HomeAssistant, config_entry, async_add_entities):
    entities = []

    unique_id = config_entry.data[CONF_EMAIL]
    web_boiler_client = hass.data[DOMAIN][unique_id][WEB_BOILER_CLIENT]
    for device in web_boiler_client.data.values():
        entities.append(WebBoilerWebsocketStatus(hass, web_boiler_client, device))
    async_add_entities(entities)


class WebBoilerWebsocketStatus(BinarySensorEntity):
    """Representation of Centrometal Boiler System websocket connection status."""

    def __init__(self, hass: HomeAssistant, web_boiler_client, device) -> None:
        """Initialize the binary sensor."""
        super().__init__()
        self.hass = hass
        self.web_boiler_client = web_boiler_client
        self.device = device
        self._serial = device["serial"]
        self._unique_id = self._serial + "_websocket_status"
        self._name = format_name(hass, device, "Centrometal Boiler System connection")
        self._signal = f"{DOMAIN}_connectivity_{device.username}"

    async def async_added_to_hass(self):
        """Subscribe to events.

        The client exposes a single connectivity callback, so with several
        boilers on one account each entity would overwrite the previous one.
        Register a dispatcher instead, and let every entity listen to it.
        """
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._signal, self.update_callback
            )
        )

        connectivity_callbacks = self.hass.data[DOMAIN][self.device.username
            ].setdefault(WEB_BOILER_CONNECTIVITY, {})
        if not connectivity_callbacks.get("registered"):
            connectivity_callbacks["registered"] = True

            async def _forward(status):
                async_dispatcher_send(self.hass, self._signal, status)

            self.web_boiler_client.set_connectivity_callback(_forward)

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._unique_id

    @property
    def is_on(self) -> bool:
        """Return the status of the sensor."""
        return self.web_boiler_client.is_websocket_connected()

    @property
    def should_poll(self) -> bool:
        """No polling needed for a sensor."""
        return False

    def update_callback(self, status):
        """Call update for Home Assistant when the connectivity changes."""
        self.async_write_ha_state()

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return BinarySensorDeviceClass.CONNECTIVITY

    @property
    def device_info(self):
        return create_device_info(self.device)
