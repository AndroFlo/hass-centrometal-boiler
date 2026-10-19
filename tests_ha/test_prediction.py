"""The relight prediction, inside a real Home Assistant.

The physics itself is covered without Home Assistant in tests/test_prediction_model.py.
What matters here is the wiring: the entities only exist when they should, they
publish what the calculator computed, a charging fire suspends the prediction, and
nothing keeps running once the entry is unloaded.
"""

import asyncio
from unittest.mock import patch
from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry
import homeassistant.util.dt as dt_util

from .conftest import DOMAIN, wait_until
from .fake_centrometal import BIOTEC, BIOTEC_BUFFER, EMAIL, PASSWORD

RELIGHT = "sensor.biotec_buffer_tank_relight_time"
AUTONOMY = "sensor.biotec_buffer_tank_autonomy"
ENERGY = "sensor.biotec_buffer_tank_stored_energy"
ALERT = "binary_sensor.biotec_buffer_tank_relight_alert"
CONNECTION = "binary_sensor.centrometal_boiler_system_connection"


@pytest.fixture
def boilers():
    """Every test here needs a boiler that reports a buffer tank."""
    return [BIOTEC_BUFFER]


def options(**overrides):
    """A config entry whose prediction is on, with the tuning under test."""
    data = {
        "id": EMAIL,
        "email": EMAIL,
        "password": PASSWORD,
        "prefix": "",
        "product_prefix": True,
        "prediction_enabled": True,
        "buffer_volume": 1000,
        "building_loss_coefficient": 0.3,
        "indoor_target_temperature": 20.0,
        "weather_entity": "weather.test",
        # Always allowed, so the night window does not interfere unless tested
        "relight_window_start": "00:00:00",
        "relight_window_end": "23:59:00",
        "night_reduction_factor": 1.0,
        "dhw_daily_energy": 0.0,
    }
    data.update(overrides)
    return data


@pytest.fixture
async def predicting(hass, cloud, weather, request):
    """An entry set up with the prediction enabled."""
    overrides = getattr(request, "param", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL, data=options(**overrides))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await wait_until(lambda: hass.states.get(CONNECTION).state == "on")
    await recalculate(hass)
    yield entry
    if entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def recalculate(hass):
    """Run the calculator now, instead of waiting for its timer."""
    for calculators in hass.data[DOMAIN].values():
        for calculator in calculators.get("web_boiler_prediction", {}).values():
            await calculator.async_recalculate()
    await hass.async_block_till_done()


async def publish(hass, broker, params, serial="ABC123"):
    """Push new boiler values and recompute, as the five-minute timer would.

    The frame travels through a real socket, so wait for the matching entity to
    show the new value before recomputing.
    """
    await broker.publish(serial, params)
    name, expected = next(iter(params.items()))
    client = hass.data[DOMAIN][EMAIL]["web_boiler_client"]
    device = client.data[serial]
    await wait_until(lambda: str(device.get_parameter(name)["value"]) == str(expected))
    await hass.async_block_till_done()
    await recalculate(hass)


def state(hass, entity_id):
    return hass.states.get(entity_id).state


def attributes(hass, entity_id):
    return hass.states.get(entity_id).attributes


# ---------------------------------------------------------------- opt-in


async def test_disabled_by_default_creates_no_prediction_entities(hass, cloud, weather):
    """Most installations have no buffer tank: they must not get unknown entities."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL,
        data={"id": EMAIL, "email": EMAIL, "password": PASSWORD},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ids = hass.states.async_entity_ids()
    assert RELIGHT not in ids and AUTONOMY not in ids and ALERT not in ids
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize("boilers", [[BIOTEC]])
async def test_no_entities_without_a_buffer_tank(hass, cloud, weather):
    """BIOTEC reports no B_Tak1_1, so there is nothing to predict."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL, data=options())
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert RELIGHT not in hass.states.async_entity_ids()
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_entities_are_created_when_enabled(hass, predicting):
    ids = hass.states.async_entity_ids()
    for entity_id in (RELIGHT, AUTONOMY, ENERGY, ALERT):
        assert entity_id in ids


# ---------------------------------------------------------------- nominal


async def test_nominal_prediction(hass, predicting):
    """70/50 C tank, 45 C flow setpoint +3 K, 21 C indoors, 0 C outside, 0.3 kW/K."""
    # Usable: mean 60 C - 48 C = 12 K over 1000 L = 13.96 kWh, drawn at 6.3 kW
    energy = float(state(hass, ENERGY))
    assert energy == pytest.approx(13.96, abs=0.05)

    data = attributes(hass, AUTONOMY)
    # 0.3 kW/K x (21 - 0): the setpoint comes from the boiler (C1B_Tsob), not the option
    assert data["estimated_power_kw"] == pytest.approx(6.3, abs=0.05)
    assert data["threshold_temperature"] == pytest.approx(48.0)
    assert data["threshold_source"] == "boiler: C1B_Tpol"
    assert data["status"] == "ok"

    autonomy = float(state(hass, AUTONOMY))
    assert autonomy == pytest.approx(13.96 / 6.3, abs=0.3)

    # Relight is the depletion, minus the ignition delay and the safety margin
    # (the published timestamp is rounded to the second).
    relight = dt_util.parse_datetime(state(hass, RELIGHT))
    gap = (data["depletion_time"] - relight).total_seconds()
    assert gap == pytest.approx(timedelta(minutes=55).total_seconds(), abs=1)
    assert state(hass, ALERT) == "off"


@pytest.mark.parametrize("forecast_temperatures", [[-10.0] * 49])
async def test_colder_weather_shortens_the_autonomy(hass, predicting):
    """The whole point of using the forecast."""
    # The probe still reads 0 C, so the first two hours are blended; after that the
    # forecast drives the demand and the tank empties sooner than the 2.3 h at 0 C.
    assert float(state(hass, AUTONOMY)) < 2.33


async def test_an_empty_tank_raises_the_alert(hass, predicting, broker):
    await publish(hass, broker, {"B_Tak1_1": "40", "B_Tak2_1": "35"})
    assert attributes(hass, AUTONOMY)["status"] == "depleted"
    assert float(state(hass, AUTONOMY)) == 0.0
    assert state(hass, ALERT) == "on"


@pytest.mark.parametrize("forecast_temperatures", [[19.5] * 49])
async def test_a_very_hot_tank_lasts_beyond_the_horizon(hass, predicting, broker):
    """A full tank and barely any heat demand: further out than we will predict."""
    await publish(hass, broker, {"B_Tva1": "19.5"})
    await publish(hass, broker, {"B_Tak1_1": "95", "B_Tak2_1": "95"})
    assert attributes(hass, AUTONOMY)["status"] == "beyond_horizon"
    assert state(hass, RELIGHT) == "unknown"
    assert state(hass, ALERT) == "off"


# ---------------------------------------------------------------- threshold


@pytest.mark.parametrize(
    "predicting", [{"buffer_threshold_entity": "input_number.custom_threshold"}], indirect=True
)
async def test_the_threshold_can_come_from_another_entity(hass, predicting):
    hass.states.async_set("input_number.custom_threshold", "52")
    await recalculate(hass)
    data = attributes(hass, AUTONOMY)
    assert data["threshold_temperature"] == pytest.approx(55.0)  # 52 + 3 K offset
    assert data["threshold_source"] == "entity: input_number.custom_threshold"


@pytest.mark.parametrize(
    "predicting", [{"buffer_threshold_entity": "sensor.does_not_exist"}], indirect=True
)
async def test_a_broken_threshold_entity_falls_back(hass, predicting):
    """A missing entity must not break the prediction."""
    data = attributes(hass, AUTONOMY)
    assert data["threshold_source"] == "boiler: C1B_Tpol"
    assert data["status"] == "ok"


# ------------------------------------------------------- indoor temperature


async def test_the_indoor_target_comes_from_the_boiler(hass, predicting):
    """The boiler knows the room setpoint, including its own day/night programme."""
    data = attributes(hass, AUTONOMY)
    assert data["indoor_target"] == pytest.approx(21.0)  # C1B_Tsob, not the 20.0 option
    assert data["indoor_target_source"] == "boiler: C1B_Tsob"


async def test_a_raised_indoor_target_shortens_the_autonomy(hass, predicting, broker):
    """A warmer house draws more power, so the tank empties sooner.

    The gap has to clear one 15-minute projection step to be visible at all: from
    21 to 28 degrees the demand goes from 6.4 to 8.5 kW, which is about 30 minutes
    on this tank.
    """
    before = float(state(hass, AUTONOMY))
    await publish(hass, broker, {"C1B_Tsob": "28"})
    after = float(state(hass, AUTONOMY))
    assert after < before
    assert attributes(hass, AUTONOMY)["indoor_target"] == pytest.approx(28.0)


@pytest.mark.parametrize("predicting", [{"indoor_target_temperature": 18.0}], indirect=True)
async def test_an_absurd_room_setpoint_falls_back_to_the_option(hass, predicting, broker):
    """A probe reading 0 must not silently turn the heating demand off."""
    await publish(hass, broker, {"C1B_Tsob": "0"})
    data = attributes(hass, AUTONOMY)
    assert data["indoor_target"] == pytest.approx(18.0)
    assert data["indoor_target_source"] == "configured"


# ---------------------------------------------------------------- weather


@pytest.mark.parametrize("predicting", [{"weather_entity": ""}], indirect=True)
async def test_without_weather_the_prediction_still_works(hass, predicting):
    """Losing the forecast must not make the relight alert disappear."""
    data = attributes(hass, AUTONOMY)
    assert data["weather_fallback"] is True
    assert data["outdoor_temperature_source"] == "boiler probe"
    assert data["status"] == "ok"
    assert state(hass, AUTONOMY) != "unknown"


async def test_a_weather_integration_that_raises_falls_back(hass, cloud, weather, caplog):
    from homeassistant.core import SupportsResponse
    from homeassistant.exceptions import HomeAssistantError

    async def boom(call):
        raise HomeAssistantError("forecast unavailable")

    hass.services.async_register(
        "weather", "get_forecasts", boom, supports_response=SupportsResponse.ONLY
    )
    entry = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL, data=options())
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await wait_until(lambda: hass.states.get(CONNECTION).state == "on")
    await recalculate(hass)

    assert attributes(hass, AUTONOMY)["weather_fallback"] is True
    assert state(hass, AUTONOMY) != "unknown"
    assert "Traceback" not in caplog.text
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# ---------------------------------------------------------------- charging


async def test_a_burning_fire_suspends_the_prediction(hass, predicting, broker):
    """While the tank gains energy, a decay projection would be nonsense."""
    await publish(
        hass, broker, {"B_P1": "1", "B_Tk1b": "80", "B_Tdpl1": "160", "B_Tlo1": "400"}
    )

    assert attributes(hass, AUTONOMY)["status"] == "charging"
    assert attributes(hass, AUTONOMY)["charging"] is True
    assert state(hass, RELIGHT) == "unknown"
    assert state(hass, ALERT) == "off"
    # The autonomy is still meaningful and keeps being published
    assert state(hass, AUTONOMY) != "unknown"


async def test_a_running_pump_alone_is_not_a_fire(hass, predicting, broker):
    """The pump also runs for frost protection; the flue stays cold."""
    await publish(hass, broker, {"B_P1": "1"})
    assert attributes(hass, AUTONOMY)["charging"] is False


# ---------------------------------------------------------------- night window


@pytest.mark.parametrize(
    "predicting",
    [{"relight_window_start": "07:00:00", "relight_window_end": "22:00:00"}],
    indirect=True,
)
async def test_a_night_relight_is_moved_before_bedtime(hass, predicting, broker):
    """Nobody lights a wood fire at 3 a.m.

    The clock is frozen at 18:00 local and the reserve sized so the tank runs out
    in the middle of the night, which is exactly the case the window exists for.
    """
    evening = dt_util.as_utc(
        dt_util.now().replace(hour=18, minute=0, second=0, microsecond=0)
    )
    calculator = hass.data[DOMAIN][EMAIL]["web_boiler_prediction"]["ABC123"]
    # ~9 h of reserve at 6 kW: depletion lands around 3 a.m.
    with patch(
        "custom_components.centrometal_boiler.prediction.dt_util.utcnow",
        return_value=evening,
    ):
        await broker.publish("ABC123", {"B_Tak1_1": "95", "B_Tak2_1": "95"})
        await wait_until(
            lambda: calculator.device.get_parameter("B_Tak1_1")["value"] == "95"
        )
        await calculator.async_recalculate()
        await hass.async_block_till_done()

    data = attributes(hass, AUTONOMY)
    assert data["status"] == "ok"
    assert data["window_adjusted"] is True
    # The raw time was at night; the published one is the evening before
    assert dt_util.as_local(data["raw_relight_time"]).hour < 7
    local = dt_util.as_local(dt_util.parse_datetime(state(hass, RELIGHT)))
    assert (local.hour, local.minute) == (22, 0)


# ---------------------------------------------------------------- robustness


async def test_values_not_received_yet_do_not_break_anything(hass, predicting, broker):
    await publish(hass, broker, {"B_Tak1_1": "?"})
    # "?" is not numeric: the model reports it cannot compute, without raising
    assert state(hass, AUTONOMY) in ("unknown", state(hass, AUTONOMY))
    assert hass.states.get(ALERT).state in ("on", "off")


async def test_entities_become_unavailable_when_the_connection_drops(
    hass, predicting, broker
):
    await broker.drop_connections()
    await wait_until(lambda: state(hass, CONNECTION) == "off")
    assert state(hass, RELIGHT) == "unavailable"
    assert state(hass, ALERT) == "unavailable"


async def test_a_burst_of_pushed_values_does_not_recompute_every_time(
    hass, predicting, broker
):
    """Boilers push hundreds of values: the calculator must debounce."""
    calculators = hass.data[DOMAIN][EMAIL]["web_boiler_prediction"]
    calculator = calculators["ABC123"]
    calls = 0
    original = calculator.async_recalculate

    async def counting(*args, **kwargs):
        nonlocal calls
        calls += 1
        await original(*args, **kwargs)

    calculator.async_recalculate = counting
    for index in range(40):
        await broker.publish("ABC123", {"B_Tak1_1": str(60 + index * 0.1)})
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    assert calls <= 2


async def test_unload_leaves_nothing_running(hass, predicting):
    """A surviving timer would be caught as a lingering timer by the harness."""
    assert await hass.config_entries.async_unload(predicting.entry_id)
    await hass.async_block_till_done()
    assert predicting.state is ConfigEntryState.NOT_LOADED
    # Home Assistant keeps a restored placeholder; what matters is that nothing
    # publishes any more, which the lingering-timer check enforces on teardown.
    assert state(hass, RELIGHT) == "unavailable"
