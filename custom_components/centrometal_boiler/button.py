"""Support for boiler buttons (one-shot commands)."""

from homeassistant.const import (
    CONF_EMAIL,
)

import logging

from .buttons.WebBoilerPelletModeButton import WebBoilerPelletModeButton

from .const import DOMAIN, WEB_BOILER_CLIENT

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the buttons platform."""
    entities = []
    unique_id = config_entry.data[CONF_EMAIL]
    web_boiler_client = hass.data[DOMAIN][unique_id][WEB_BOILER_CLIENT]
    for device in web_boiler_client.data.values():
        # Only the BioTec Plus burns both wood and pellets.
        if device["type"] == "biopl":
            entities.append(WebBoilerPelletModeButton(hass, device))

    _LOGGER.debug(
        "Adding boiler commands as buttons: %s (%s)",
        entities,
        web_boiler_client.username,
    )
    if len(entities) > 0:
        async_add_entities(entities)
