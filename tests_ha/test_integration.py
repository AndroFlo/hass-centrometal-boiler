"""The integration inside a real Home Assistant, against the fake Centrometal cloud."""

import asyncio
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError

from .conftest import DOMAIN, wait_until
from .fake_centrometal import BIOTEC, EMAIL, PASSWORD, PELTEC

BUTTON = "button.biotec_pellet_mode"
SWITCH = "switch.biotec_boiler_switch"
CONNECTION = "binary_sensor.centrometal_boiler_system_connection"
SCCMD = ("/api/inst/control/4242", {"cmd-name": "SCCMD", "cmd-value": 1})


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await wait_until(lambda: hass.states.get(CONNECTION).state == "on")


def state(hass, entity_id):
    return hass.states.get(entity_id).state


# ---------------------------------------------------------------- setup


@pytest.mark.parametrize("boilers", [[BIOTEC, PELTEC]])
async def test_only_the_biotec_plus_gets_entities(hass, entry):
    await setup(hass, entry)
    ids = hass.states.async_entity_ids()
    assert "sensor.biotec_boiler_state" in ids
    assert "sensor.biotec_boiler_temperature_wood" in ids
    assert SWITCH in ids and BUTTON in ids and "switch.circuit_1" in ids
    # The ignored PelTec neither gets entities nor prefixes the names with serial numbers
    assert not any("pel999" in i or "abc123" in i for i in ids)


async def test_values_and_counters(hass, entry):
    await setup(hass, entry)
    assert state(hass, "sensor.biotec_boiler_temperature_wood") == "65"
    assert state(hass, "sensor.biotec_boiler_state") == "ON"
    assert state(hass, SWITCH) == "on"
    assert state(hass, "switch.circuit_1") == "on"
    # Counters not received yet are unknown, not rejected by Home Assistant
    assert state(hass, "sensor.biotec_startup_wood") == "unknown"


async def test_refresh_when_connected(hass, entry, website):
    with patch("centrometal_web_boiler.WebBoilerClient.WebBoilerClient.refresh") as refresh:
        await setup(hass, entry)
        await hass.async_block_till_done()
    refresh.assert_called()


async def test_cloud_down_retries_later(hass, entry, website):
    website.down = True
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_wrong_password_asks_for_credentials(hass, cloud):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=EMAIL, data={"id": EMAIL, "email": EMAIL, "password": "old"}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


@pytest.mark.parametrize("boilers", [[PELTEC]])
async def test_no_biotec_plus(hass, entry):
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR


# ---------------------------------------------------------------- connection


@pytest.mark.parametrize("broker_delay", [0.5])
async def test_entities_become_available_when_connected(hass, entry):
    """The real cloud accepts the WebSocket after the entities are created."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert state(hass, BUTTON) == "unavailable"
    assert state(hass, CONNECTION) == "off"
    await wait_until(lambda: state(hass, BUTTON) != "unavailable")
    assert state(hass, CONNECTION) == "on"
    assert state(hass, SWITCH) == "on"


async def test_connection_lost_and_restored(hass, entry, broker):
    await setup(hass, entry)
    with patch("custom_components.centrometal_boiler.WEB_BOILER_LOGIN_RETRY_INTERVAL", 0):
        await broker.drop_connections()
        await wait_until(lambda: state(hass, CONNECTION) == "off")
        assert state(hass, BUTTON) == "unavailable"
        # The watchdog logs in again and reconnects
        await wait_until(lambda: state(hass, CONNECTION) == "on", timeout=5)
    assert state(hass, BUTTON) != "unavailable"


# ---------------------------------------------------------------- commands


async def test_pellet_mode_button(hass, entry, website):
    await setup(hass, entry)
    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
    assert website.commands[-1] == SCCMD


async def test_switches(hass, entry, website):
    await setup(hass, entry)
    await hass.services.async_call("switch", "turn_off", {"entity_id": SWITCH}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.circuit_1"}, blocking=True)
    assert ("/api/inst/control/4242", {"cmd-name": "CMD", "cmd-value": 0}) in website.commands
    assert ("/api/inst/control/multiple", {"messages": {"4242": {"PWR 72": 1}}}) in website.commands


async def test_refused_command_is_retried_then_reported(hass, entry, website):
    await setup(hass, entry)
    website.command_status = "error"
    with pytest.raises(HomeAssistantError, match="refused"):
        await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
    # Sent, then sent again after logging in again
    assert website.commands.count(SCCMD) == 2


# ---------------------------------------------------------------- config flow


async def test_config_flow(hass, cloud):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_config_flow_cloud_down(hass, cloud):
    cloud.down = True
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": PASSWORD}
    )
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize("boilers", [[PELTEC]])
async def test_config_flow_no_biotec_plus(hass, cloud):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": PASSWORD}
    )
    assert result["errors"] == {"base": "no_devices"}


async def test_options_flow(hass, entry):
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"prefix": "Home", "product_prefix": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.data["prefix"] == "Home"
    await wait_until(lambda: entry.state is ConfigEntryState.LOADED)
    await asyncio.sleep(0)
