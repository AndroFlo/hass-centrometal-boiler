"""Config flow for Centrometal boiler integration."""
from collections import OrderedDict
import logging

from centrometal_web_boiler import WebBoilerClient

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_ID, CONF_PASSWORD, CONF_PREFIX
from homeassistant.core import callback

from .const import CONF_PRODUCT_PREFIX, DOMAIN

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
        return CentrometalBoilerOptionsFlowHandler(config_entry)

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
    """Allow changing the naming options without recreating the entry."""

    def __init__(self, config_entry) -> None:
        """Initialize the options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            data = dict(self.config_entry.data)
            data[CONF_PREFIX] = user_input.get(CONF_PREFIX, "")
            data[CONF_PRODUCT_PREFIX] = user_input.get(CONF_PRODUCT_PREFIX, True)
            self.hass.config_entries.async_update_entry(self.config_entry, data=data)
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(title="", data={})

        fields = OrderedDict()
        fields[
            vol.Optional(
                CONF_PREFIX, default=self.config_entry.data.get(CONF_PREFIX, "")
            )
        ] = str
        fields[
            vol.Optional(
                CONF_PRODUCT_PREFIX,
                default=self.config_entry.data.get(CONF_PRODUCT_PREFIX, True),
            )
        ] = bool

        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))


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

        if len(web_boiler_client.data) == 0:
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
