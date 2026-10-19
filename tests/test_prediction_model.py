"""The buffer tank physics, checked without Home Assistant.

prediction_model.py imports nothing from Home Assistant on purpose, so these run in
milliseconds and cover the parts that are easy to get subtly wrong: the energy that
is actually usable, the walk through the forecast, and the night window that turns
"relight at 03:05" into "load the stove before 22:00".
"""

import pathlib
import sys
from datetime import datetime, time, timedelta, timezone

import pytest

sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "centrometal_boiler"),
)

import prediction_model as model  # noqa: E402

PARIS = timezone(timedelta(hours=2))
DAY = time(7, 0)
NIGHT = time(22, 0)


def at(hour, minute=0, day=19):
    return datetime(2026, 10, day, hour, minute, tzinfo=PARIS)


def flat_forecast(start, temperature, hours=48):
    return [(start + timedelta(hours=h), temperature) for h in range(hours + 1)]


# ---------------------------------------------------------------- stored energy


def test_stored_energy_is_volume_times_usable_temperature():
    # 1000 L, mean 60 C, threshold 40 C -> 20 K usable
    energy = model.stored_energy_kwh(1000, 70, 50, 40)
    assert energy == pytest.approx(1000 * 1.163e-3 * 20, rel=1e-6)
    assert energy == pytest.approx(23.26, abs=0.01)


def test_energy_below_the_threshold_does_not_count():
    """The tank still holds heat, but none of it can reach the radiators."""
    assert model.stored_energy_kwh(1000, 35, 30, 45) == 0.0


def test_stratification_factor_weights_the_probes():
    top_only = model.stored_energy_kwh(1000, 80, 40, 0, stratification_factor=1.0)
    bottom_only = model.stored_energy_kwh(1000, 80, 40, 0, stratification_factor=0.0)
    balanced = model.stored_energy_kwh(1000, 80, 40, 0, stratification_factor=0.5)
    assert top_only > balanced > bottom_only
    assert balanced == pytest.approx((top_only + bottom_only) / 2)


def test_stratification_factor_is_clamped():
    assert model.buffer_mean_temperature(80, 40, 5.0) == 80
    assert model.buffer_mean_temperature(80, 40, -5.0) == 40


# ---------------------------------------------------------------- heating power


def test_heating_power_follows_the_temperature_difference():
    assert model.heating_power_kw(0.3, 20, 0) == pytest.approx(6.0)
    assert model.heating_power_kw(0.3, 20, 10) == pytest.approx(3.0)


def test_no_heating_when_it_is_warmer_outside_but_hot_water_still_draws():
    assert model.heating_power_kw(0.3, 20, 25) == 0.0
    assert model.heating_power_kw(0.3, 20, 25, dhw_power_kw=0.125) == pytest.approx(0.125)


def test_night_factor_reduces_the_heating_term_only():
    full = model.heating_power_kw(0.3, 20, 0, 1.0, dhw_power_kw=0.1)
    reduced = model.heating_power_kw(0.3, 20, 0, 0.8, dhw_power_kw=0.1)
    assert reduced == pytest.approx((full - 0.1) * 0.8 + 0.1)


# ---------------------------------------------------------------- observed power


def test_observed_power_from_the_cooling_rate():
    # 1000 L losing 5 K in 1 h
    assert model.observed_power_kw(1000, 60, 55, 1.0) == pytest.approx(1000 * 1.163e-3 * 5)


def test_observed_power_is_undefined_while_the_tank_warms_up():
    """A fire is burning: the cooling rate says nothing about the heat demand."""
    assert model.observed_power_kw(1000, 55, 60, 1.0) is None
    assert model.observed_power_kw(1000, 60, 55, 0.0) is None


# ---------------------------------------------------------------- forecast


def test_interpolate_between_two_points():
    forecast = [(at(12), 10.0), (at(13), 20.0)]
    assert model.interpolate_forecast(forecast, at(12, 30)) == pytest.approx(15.0)


def test_interpolate_holds_the_nearest_value_outside_the_range():
    forecast = [(at(12), 10.0), (at(13), 20.0)]
    assert model.interpolate_forecast(forecast, at(8)) == 10.0
    assert model.interpolate_forecast(forecast, at(20)) == 20.0
    assert model.interpolate_forecast([], at(12)) is None


def test_outdoor_blends_from_the_probe_to_the_forecast():
    """The probe is right now, the forecast is later: no step between the two."""
    now = at(12)
    forecast = flat_forecast(now, 0.0)
    assert model.outdoor_at(now, now, forecast, probe=10.0) == pytest.approx(10.0)
    # Halfway through the blend
    assert model.outdoor_at(now + timedelta(hours=1), now, forecast, 10.0) == pytest.approx(5.0)
    # Past it, the forecast alone
    assert model.outdoor_at(now + timedelta(hours=5), now, forecast, 10.0) == pytest.approx(0.0)


def test_outdoor_falls_back_to_whichever_source_exists():
    now = at(12)
    assert model.outdoor_at(now, now, [], probe=7.0) == 7.0
    assert model.outdoor_at(now, now, flat_forecast(now, 3.0), probe=None) == 3.0
    assert model.outdoor_at(now, now, [], probe=None) is None


# ---------------------------------------------------------------- projection


def project(energy, outdoor, now=None, **kwargs):
    now = now or at(12)
    options = {
        "loss_coefficient": 0.3,
        "indoor_target": 20.0,
        "night_factor": 1.0,
        "night_start": NIGHT,
        "night_end": DAY,
        "dhw_power_kw": 0.0,
    }
    options.update(kwargs)
    return model.project_depletion(
        energy, now, flat_forecast(now, outdoor), outdoor, **options
    )


def test_depletion_matches_energy_over_power_at_a_constant_temperature():
    now = at(12)
    # 0.3 kW/K x 20 K = 6 kW, 24 kWh -> 4 h
    result = project(24.0, 0.0, now, night_factor=1.0)
    assert result.power_now_kw == pytest.approx(6.0)
    expected = now + timedelta(hours=4)
    assert abs((result.depletion - expected).total_seconds()) <= model.PROJECTION_STEP.total_seconds()


def test_an_empty_tank_is_already_depleted():
    now = at(12)
    result = project(0.0, 0.0, now)
    assert result.depletion == now
    assert result.beyond_horizon is False


def test_a_tank_that_outlasts_the_horizon_is_reported_as_such():
    result = project(10_000.0, 0.0)
    assert result.depletion is None
    assert result.beyond_horizon is True


def test_no_demand_at_all_never_depletes_the_tank():
    """Warmer outside than the target, and no hot water: nothing is drawn."""
    result = project(10.0, 25.0)
    assert result.depletion is None
    assert result.beyond_horizon is True


def test_colder_weather_shortens_the_autonomy():
    mild = project(24.0, 10.0).depletion
    cold = project(24.0, -10.0).depletion
    assert cold < mild


def test_heating_off_only_affects_the_reported_power():
    result = project(24.0, 0.0, heating_off=True, dhw_power_kw=0.125)
    assert result.power_now_kw == pytest.approx(0.125)


def test_projection_without_any_temperature_source_gives_up():
    now = at(12)
    result = model.project_depletion(
        24.0, now, [], None, 0.3, 20.0, 1.0, NIGHT, DAY, 0.0
    )
    assert result.depletion is None
    assert result.beyond_horizon is True


# ---------------------------------------------------------------- night window


def test_a_relight_time_inside_the_window_is_left_alone():
    raw = at(18)
    result = model.apply_relight_window(raw, at(12), DAY, NIGHT)
    assert result.moment == raw
    assert result.window_adjusted is False
    assert result.late is False


def test_a_night_relight_is_moved_back_before_bedtime():
    """The whole point: nobody lights a wood fire at 03:05."""
    raw = at(3, 5, day=20)
    result = model.apply_relight_window(raw, at(18), DAY, NIGHT)
    assert result.moment == at(22, 0, day=19)
    assert result.raw == raw
    assert result.window_adjusted is True
    assert result.late is False


def test_an_early_morning_relight_is_moved_back_to_the_night_before():
    raw = at(6, 0)
    result = model.apply_relight_window(raw, at(20, 0, day=18), DAY, NIGHT)
    assert result.moment == at(22, 0, day=18)
    assert result.window_adjusted is True


def test_a_missed_window_says_so_instead_of_publishing_a_past_time():
    """A silent past timestamp would never fire the notification."""
    now = at(2, 0, day=20)
    raw = at(3, 0, day=20)
    result = model.apply_relight_window(raw, now, DAY, NIGHT)
    assert result.moment == now
    assert result.window_adjusted is True
    assert result.late is True


def test_a_window_wrapping_midnight_is_supported():
    # Someone who can only light the fire in the evening and at night
    start, end = time(20, 0), time(8, 0)
    assert model._in_window(at(23), start, end) is True
    assert model._in_window(at(7), start, end) is True
    assert model._in_window(at(14), start, end) is False


def test_the_night_window_is_judged_in_local_time():
    """Midnight UTC is the evening or the morning depending on where you live.

    Without the converter the setback would apply at the wrong hours outside UTC,
    and the relight window would let through a 3 a.m. relight.
    """
    utc_morning = datetime(2026, 10, 19, 9, 20, tzinfo=timezone.utc)
    assert model.is_night(utc_morning, NIGHT, DAY) is False

    def to_pacific(moment):
        return moment.astimezone(timezone(timedelta(hours=-7)))

    # The same instant is 02:20 in California: the middle of the night
    assert model.is_night(utc_morning, NIGHT, DAY, to_pacific) is True


def test_is_night_handles_both_orders():
    assert model.is_night(at(23), NIGHT, DAY) is True
    assert model.is_night(at(3), NIGHT, DAY) is True
    assert model.is_night(at(12), NIGHT, DAY) is False
    # A window that does not wrap
    assert model.is_night(at(12), time(10), time(14)) is True
    # Degenerate window: never night
    assert model.is_night(at(12), DAY, DAY) is False
