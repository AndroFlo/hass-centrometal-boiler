"""Config flow for Centrometal boiler integration."""
from collections import OrderedDict
import logging

from centrometal_web_boiler import WebBoilerClient

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_ID, CONF_PASSWORD, CONF_PREFIX
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BUFFER_THRESHOLD,
    CONF_BUFFER_THRESHOLD_ENTITY,
    CONF_BUFFER_THRESHOLD_OFFSET,
    CONF_BUFFER_VOLUME,
    CONF_BUILDING_LOSS,
    CONF_DHW_DAILY,
    CONF_IGNITION_DELAY,
    CONF_INDOOR_TARGET,
    CONF_NIGHT_END,
    CONF_NIGHT_FACTOR,
    CONF_NIGHT_START,
    CONF_PREDICTION_ENABLED,
    CONF_PRODUCT_PREFIX,
    CONF_RELIGHT_WINDOW_END,
    CONF_RELIGHT_WINDOW_START,
    CONF_SAFETY_MARGIN,
    CONF_STRATIFICATION_FACTOR,
    CONF_WEATHER_ENTITY,
    DEFAULT_BUFFER_THRESHOLD,
    DEFAULT_BUFFER_THRESHOLD_OFFSET,
    DEFAULT_BUFFER_VOLUME,
    DEFAULT_BUILDING_LOSS,
    DEFAULT_DHW_DAILY,
    DEFAULT_IGNITION_DELAY,
    DEFAULT_INDOOR_TARGET,
    DEFAULT_NIGHT_END,
    DEFAULT_NIGHT_FACTOR,
    DEFAULT_NIGHT_START,
    DEFAULT_PREDICTION_ENABLED,
    DEFAULT_RELIGHT_WINDOW_END,
    DEFAULT_RELIGHT_WINDOW_START,
    DEFAULT_SAFETY_MARGIN,
    DEFAULT_STRATIFICATION_FACTOR,
    DOMAIN,
    SUPPORTED_DEVICE_TYPE,
)


def _number(minimum, maximum, step, unit=None):
    """A number box, which beats a free-text field for a physical quantity."""
    config = {
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": selector.NumberSelectorMode.BOX,
    }
    if unit is not None:
        # The key must be absent rather than None: the schema expects a string.
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(selector.NumberSelectorConfig(**config))

_LOGGER = logging.getLogger(__name__)

# pylint: disable=missing-function-docstring
# pylint: disable=broad-except


class CannotConnect(Exception):
    """Raised when the Centrometal server cannot be reached."""


class InvalidAuth(Exception):
    """Raised when the credentials are rejected by the Centrometal server."""


class NoDeviceFound(Exception):
    """Raised when the account has no boiler attached to it."""


class CentrometalBoilerConfigFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Centrometal boiler."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return CentrometalBoilerOptionsFlowHandler()

    async def _show_setup_form(self, errors=None):
        """Show the setup form to the user."""
        fields = OrderedDict()
        fields[vol.Required(CONF_EMAIL)] = str
        fields[vol.Required(CONF_PASSWORD)] = str
        fields[vol.Optional(CONF_PREFIX, default="")] = str
        fields[vol.Optional(CONF_PRODUCT_PREFIX, default=True)] = bool

        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(fields), errors=errors or {}
        )

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is None:
            return await self._show_setup_form()

        try:
            device_collection = await try_connection(
                user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
        except InvalidAuth:
            _LOGGER.warning("Invalid credentials for %s", user_input[CONF_EMAIL])
            return await self._show_setup_form({"base": "invalid_auth"})
        except NoDeviceFound:
            _LOGGER.warning("No boiler found for %s", user_input[CONF_EMAIL])
            return await self._show_setup_form({"base": "no_devices"})
        except CannotConnect:
            _LOGGER.warning("Cannot reach the Centrometal server")
            return await self._show_setup_form({"base": "cannot_connect"})
        except Exception:
            _LOGGER.exception("Unexpected exception " + user_input[CONF_EMAIL])
            return await self._show_setup_form({"base": "unknown"})

        unique_id = user_input[CONF_EMAIL]
        data = {
            CONF_ID: unique_id,
            CONF_EMAIL: user_input[CONF_EMAIL],
            CONF_PASSWORD: user_input[CONF_PASSWORD],
            CONF_PREFIX: user_input.get(CONF_PREFIX, ""),
            CONF_PRODUCT_PREFIX: user_input.get(CONF_PRODUCT_PREFIX, True),
        }

        if self._reauth_entry is not None:
            self.hass.config_entries.async_update_entry(self._reauth_entry, data=data)
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        device = list(device_collection.values())[0]
        title = device["product"] + ": " + device["address"] + ", " + device["place"]

        return self.async_create_entry(title=title, data=data)

    async def async_step_reauth(self, entry_data):
        """Handle a re-authentication triggered by expired credentials."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Ask the user for new credentials."""
        if user_input is None:
            return await self._show_setup_form()
        return await self.async_step_user(user_input)


class CentrometalBoilerOptionsFlowHandler(config_entries.OptionsFlow):
    """Allow changing the naming options without recreating the entry.

    self.config_entry is provided by Home Assistant (a read-only property since 2024.11:
    assigning it in __init__ fails).
    """

    async def async_step_init(self, user_input=None):
        """Offer the two groups of options: naming and relight prediction."""
        return self.async_show_menu(step_id="init", menu_options=["naming", "prediction"])

    def _current(self, key, default):
        """The value in use, wherever it is stored.

        This flow has always written into entry.data rather than entry.options;
        reading both keeps the existing behaviour while tolerating a future move.
        """
        if key in self.config_entry.options:
            return self.config_entry.options[key]
        return self.config_entry.data.get(key, default)

    async def _async_save(self, user_input):
        data = dict(self.config_entry.data)
        data.update(user_input)
        self.hass.config_entries.async_update_entry(self.config_entry, data=data)
        await self.hass.config_entries.async_reload(self.config_entry.entry_id)
        return self.async_create_entry(title="", data={})

    async def async_step_naming(self, user_input=None):
        """How the entities of this integration are named."""
        if user_input is not None:
            return await self._async_save(
                {
                    CONF_PREFIX: user_input.get(CONF_PREFIX, ""),
                    CONF_PRODUCT_PREFIX: user_input.get(CONF_PRODUCT_PREFIX, True),
                }
            )

        fields = OrderedDict()
        fields[vol.Optional(CONF_PREFIX, default=self._current(CONF_PREFIX, ""))] = str
        fields[
            vol.Optional(
                CONF_PRODUCT_PREFIX, default=self._current(CONF_PRODUCT_PREFIX, True)
            )
        ] = bool

        return self.async_show_form(step_id="naming", data_schema=vol.Schema(fields))

    async def async_step_prediction(self, user_input=None):
        """When the wood fire has to be lit again (see the README for the model)."""
        if user_input is not None:
            # An unset entity selector is absent from the input, not empty: store
            # an empty string so clearing it actually clears it.
            for key in (CONF_WEATHER_ENTITY, CONF_BUFFER_THRESHOLD_ENTITY):
                user_input.setdefault(key, "")
            return await self._async_save(user_input)

        fields = OrderedDict()
        fields[
            vol.Optional(
                CONF_PREDICTION_ENABLED,
                default=self._current(CONF_PREDICTION_ENABLED, DEFAULT_PREDICTION_ENABLED),
            )
        ] = bool
        fields[
            vol.Optional(
                CONF_BUFFER_VOLUME,
                default=self._current(CONF_BUFFER_VOLUME, DEFAULT_BUFFER_VOLUME),
            )
        ] = _number(50, 10000, 10, "L")
        fields[
            vol.Optional(
                CONF_BUILDING_LOSS,
                default=self._current(CONF_BUILDING_LOSS, DEFAULT_BUILDING_LOSS),
            )
        ] = _number(0.05, 3, 0.01, "kW/K")
        fields[
            vol.Optional(
                CONF_INDOOR_TARGET,
                default=self._current(CONF_INDOOR_TARGET, DEFAULT_INDOOR_TARGET),
            )
        ] = _number(10, 28, 0.5, "°C")
        fields[
            vol.Optional(
                CONF_WEATHER_ENTITY, default=self._current(CONF_WEATHER_ENTITY, "")
            )
        ] = vol.Any(
            selector.EntitySelector(selector.EntitySelectorConfig(domain="weather")), ""
        )
        fields[
            vol.Optional(
                CONF_BUFFER_THRESHOLD_ENTITY,
                default=self._current(CONF_BUFFER_THRESHOLD_ENTITY, ""),
            )
        ] = vol.Any(
            selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "number", "input_number"])
            ),
            "",
        )
        fields[
            vol.Optional(
                CONF_BUFFER_THRESHOLD,
                default=self._current(CONF_BUFFER_THRESHOLD, DEFAULT_BUFFER_THRESHOLD),
            )
        ] = _number(20, 80, 0.5, "°C")
        fields[
            vol.Optional(
                CONF_BUFFER_THRESHOLD_OFFSET,
                default=self._current(
                    CONF_BUFFER_THRESHOLD_OFFSET, DEFAULT_BUFFER_THRESHOLD_OFFSET
                ),
            )
        ] = _number(0, 15, 0.5, "K")
        fields[
            vol.Optional(
                CONF_IGNITION_DELAY,
                default=self._current(CONF_IGNITION_DELAY, DEFAULT_IGNITION_DELAY),
            )
        ] = _number(0, 180, 5, "min")
        fields[
            vol.Optional(
                CONF_SAFETY_MARGIN,
                default=self._current(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN),
            )
        ] = _number(0, 240, 5, "min")
        fields[
            vol.Optional(
                CONF_RELIGHT_WINDOW_START,
                default=self._current(
                    CONF_RELIGHT_WINDOW_START, DEFAULT_RELIGHT_WINDOW_START
                ),
            )
        ] = selector.TimeSelector()
        fields[
            vol.Optional(
                CONF_RELIGHT_WINDOW_END,
                default=self._current(CONF_RELIGHT_WINDOW_END, DEFAULT_RELIGHT_WINDOW_END),
            )
        ] = selector.TimeSelector()
        fields[
            vol.Optional(
                CONF_STRATIFICATION_FACTOR,
                default=self._current(
                    CONF_STRATIFICATION_FACTOR, DEFAULT_STRATIFICATION_FACTOR
                ),
            )
        ] = _number(0, 1, 0.05)
        fields[
            vol.Optional(
                CONF_DHW_DAILY, default=self._current(CONF_DHW_DAILY, DEFAULT_DHW_DAILY)
            )
        ] = _number(0, 30, 0.5, "kWh")
        fields[
            vol.Optional(
                CONF_NIGHT_FACTOR,
                default=self._current(CONF_NIGHT_FACTOR, DEFAULT_NIGHT_FACTOR),
            )
        ] = _number(0.3, 1, 0.05)
        fields[
            vol.Optional(
                CONF_NIGHT_START, default=self._current(CONF_NIGHT_START, DEFAULT_NIGHT_START)
            )
        ] = selector.TimeSelector()
        fields[
            vol.Optional(
                CONF_NIGHT_END, default=self._current(CONF_NIGHT_END, DEFAULT_NIGHT_END)
            )
        ] = selector.TimeSelector()

        return self.async_show_form(step_id="prediction", data_schema=vol.Schema(fields))


async def try_connection(email, password):
    _LOGGER.debug(
        f"Trying to connect to Centrometal boiler server during setup {email}"
    )
    web_boiler_client = WebBoilerClient()
    try:
        try:
            logged_in = await web_boiler_client.login(username=email, password=password)
        except Exception as ex:
            raise CannotConnect(
                f"Cannot reach the Centrometal boiler server {email}"
            ) from ex
        if not logged_in:
            raise InvalidAuth(f"Login to Centrometal boiler server failed {email}")

        try:
            got_configuration = await web_boiler_client.get_configuration()
        except Exception as ex:
            raise CannotConnect(
                f"Cannot read the configuration from the Centrometal server {email}"
            ) from ex
        if not got_configuration:
            raise CannotConnect(
                f"Getting devices from Centrometal boiler server failed {email}"
            )

        if not any(
            device["type"] == SUPPORTED_DEVICE_TYPE
            for device in web_boiler_client.data.values()
        ):
            raise NoDeviceFound(
                f"No device found on Centrometal boiler server {email}"
            )
        _LOGGER.debug(
            f"Successfully connected to Centrometal boiler during setup {email}"
        )
        return web_boiler_client.data
    finally:
        # Always release the aiohttp session, otherwise every failed attempt
        # leaks a connection ("Unclosed client session").
        try:
            await web_boiler_client.close_websocket()
        except Exception:
            pass
        try:
            await web_boiler_client.http_client.close_session()
        except Exception:
            pass
