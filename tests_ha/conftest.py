"""Run the integration in a real Home Assistant (pytest-homeassistant-custom-component),
connected to a local fake of the Centrometal cloud instead of web-boiler.com."""

import asyncio
import pathlib
import sys
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestServer
from centrometal_web_boiler import WebBoilerClient
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

# custom_components/ must be importable to be patched
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import custom_components.centrometal_boiler  # noqa: E402,F401
import custom_components.centrometal_boiler.config_flow  # noqa: E402,F401

from .fake_centrometal import BIOTEC, EMAIL, PASSWORD, FakeBroker, FakeWebsite  # noqa: E402

DOMAIN = "centrometal_boiler"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def boilers():
    return [BIOTEC]


@pytest.fixture
def broker_delay():
    return 0.0


@pytest.fixture
async def website(boilers, socket_enabled):
    fake = FakeWebsite(boilers)
    server = TestServer(fake.app, host="127.0.0.1")
    await server.start_server()
    fake.url = str(server.make_url("")).rstrip("/")
    yield fake
    await server.close()


@pytest.fixture
async def broker(broker_delay, socket_enabled):
    fake = FakeBroker(broker_delay)
    fake.url = await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
def cloud(website, broker):
    """Make every WebBoilerClient of the integration talk to the fake cloud."""

    def factory():
        return WebBoilerClient(webroot=website.url, stomp_url=broker.url)

    with (
        patch("custom_components.centrometal_boiler.WebBoilerClient", factory),
        patch("custom_components.centrometal_boiler.config_flow.WebBoilerClient", factory),
    ):
        yield website


@pytest.fixture
async def entry(hass, cloud):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL,
        data={"id": EMAIL, "email": EMAIL, "password": PASSWORD, "prefix": "", "product_prefix": True},
    )
    entry.add_to_hass(hass)
    yield entry
    # Unload, otherwise the tick timer and the WebSocket outlive the test
    if entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def wait_until(condition, timeout=3.0):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.05)
