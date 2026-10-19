"""Predicts when the wood fire must be lit again, per boiler.

One calculator per boiler reads the tank probes, asks the weather integration for a
forecast and runs the model of prediction_model.py. The entities subscribe to it and
publish its result; they never compute anything themselves, so the three of them
always agree.

Recomputing on every pushed parameter would be ruinous (boilers push hundreds of
values continuously), so the calculator runs on a five-minute timer and only lets an
event trigger it when a value really moved and the last run is old enough.
"""

from __future__ import annotations

import asyncio
import logging
import time as time_module
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
import homeassistant.util.dt as dt_util

from . import prediction_model as model
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
    FORECAST_CACHE_SECONDS,
    PREDICTION_CHARGE_HYSTERESIS,
    PREDICTION_INITIAL_DELAY,
    PREDICTION_INTERVAL,
    PREDICTION_MIN_INTERVAL,
    PREDICTION_TEMPERATURE_DELTA,
)

_LOGGER = logging.getLogger(__name__)

# Parameters the model reads. B_Tak* drive an event-driven recomputation, the rest
# are only read when a run happens.
PARAM_BUFFER_TOP = "B_Tak1_1"
PARAM_BUFFER_BOTTOM = "B_Tak2_1"
PARAM_OUTDOOR = "B_Tva1"
PARAM_BOILER_WOOD = "B_Tk1b"
PARAM_BOILER_PELLET = "B_Tk1p"
PARAM_FLUE_GAS = "B_Tdpl1"
PARAM_FIREBOX = "B_Tlo1"
PARAM_BOILER_PUMP = "B_P1"
PARAM_BOILER_PUMP_DEMAND = "B_zahP1"
PARAM_CIRCUIT_PUMP = "C1B_P"
PARAM_SECOND_PUMP = "B_P2"
# The flow setpoint follows the boiler's heating curve, which is what the tank must
# stay above; it is the default reference for the low threshold.
PARAM_FLOW_TARGET = "C1B_Tpol"

WATCHED_FOR_EVENTS = (PARAM_BUFFER_TOP, PARAM_BUFFER_BOTTOM, PARAM_BOILER_PUMP)

# Charging is detected from physics, not from B_STATE whose vocabulary is unknown.
CHARGE_FLUE_GAS = 80.0
CHARGE_FIREBOX = 150.0
CHARGE_BOILER_OVER_TANK = 5.0

STATUS_OK = "ok"
STATUS_CHARGING = "charging"
STATUS_DEPLETED = "depleted"
STATUS_BEYOND_HORIZON = "beyond_horizon"
STATUS_UNAVAILABLE = "unavailable"

REASON_NO_BUFFER = "buffer_temperature_unavailable"
REASON_INVALID_CONFIG = "invalid_configuration"


def option(entry: ConfigEntry, key: str, default):
    """Read an option, from wherever this integration happens to keep it.

    The options flow writes into entry.data rather than entry.options (see the
    repository's CLAUDE.md); looking at both keeps a future migration trivial.
    """
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, default)


def prediction_enabled(entry: ConfigEntry) -> bool:
    return bool(option(entry, CONF_PREDICTION_ENABLED, DEFAULT_PREDICTION_ENABLED))


def has_buffer_tank(device) -> bool:
    """Whether this boiler actually reports a buffer tank.

    Without it the entities would only ever be "unknown", so they are not created.
    """
    return _number(device, PARAM_BUFFER_TOP) is not None


def _number(device, name: str) -> float | None:
    """A boiler parameter as a float, or None when it has not been received yet."""
    if not device.has_parameter(name):
        return None
    value = device.get_parameter(name)["value"]
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_on(device, name: str) -> bool:
    return _number(device, name) == 1


def _parse_time(raw, default: str) -> time:
    parsed = dt_util.parse_time(raw if raw else default)
    return parsed if parsed is not None else dt_util.parse_time(default)


@dataclass(frozen=True)
class BufferPrediction:
    """One snapshot of the prediction, shared by every entity.

    Frozen so the entities always read a consistent set of values, never an object
    mutating under them mid-render.
    """

    status: str = STATUS_UNAVAILABLE
    unavailable_reason: str | None = None
    relight_time: datetime | None = None
    raw_relight_time: datetime | None = None
    autonomy_hours: float | None = None
    stored_energy_kwh: float | None = None
    depletion_time: datetime | None = None
    estimated_power_kw: float | None = None
    observed_power_kw: float | None = None
    buffer_mean_temperature: float | None = None
    threshold_temperature: float | None = None
    threshold_source: str | None = None
    outdoor_temperature: float | None = None
    outdoor_temperature_source: str | None = None
    weather_fallback: bool = False
    window_adjusted: bool = False
    late: bool = False
    charging: bool = False
    last_calculated: datetime | None = None

    @property
    def is_alert(self) -> bool:
        """Whether the fire should be lit now."""
        if self.charging or self.relight_time is None:
            return False
        if self.status not in (STATUS_OK, STATUS_DEPLETED):
            return False
        return dt_util.utcnow() >= self.relight_time

    def attributes(self) -> dict:
        """The numbers behind the prediction, for tuning and for the card."""
        data = {
            "status": self.status,
            "charging": self.charging,
            "stored_energy_kwh": _round(self.stored_energy_kwh, 2),
            "estimated_power_kw": _round(self.estimated_power_kw, 2),
            "observed_power_kw": _round(self.observed_power_kw, 2),
            "buffer_mean_temperature": _round(self.buffer_mean_temperature, 1),
            "threshold_temperature": _round(self.threshold_temperature, 1),
            "threshold_source": self.threshold_source,
            "outdoor_temperature": _round(self.outdoor_temperature, 1),
            "outdoor_temperature_source": self.outdoor_temperature_source,
            "weather_fallback": self.weather_fallback,
            "depletion_time": self.depletion_time,
            "raw_relight_time": self.raw_relight_time,
            "window_adjusted": self.window_adjusted,
            "late": self.late,
            "last_calculated": self.last_calculated,
        }
        if self.unavailable_reason:
            data["unavailable_reason"] = self.unavailable_reason
        return {key: value for key, value in data.items() if value is not None}


def _round(value, digits):
    return None if value is None else round(value, digits)


@dataclass
class _Sample:
    """A past mean tank temperature, to derive the power actually being drawn."""

    moment: float
    temperature: float


@dataclass
class _Settings:
    """The model's configuration, read once when the calculator is built."""

    volume: float = DEFAULT_BUFFER_VOLUME
    threshold: float = DEFAULT_BUFFER_THRESHOLD
    threshold_entity: str = ""
    threshold_offset: float = DEFAULT_BUFFER_THRESHOLD_OFFSET
    stratification: float = DEFAULT_STRATIFICATION_FACTOR
    loss_coefficient: float = DEFAULT_BUILDING_LOSS
    indoor_target: float = DEFAULT_INDOOR_TARGET
    dhw_power_kw: float = DEFAULT_DHW_DAILY / 24.0
    night_factor: float = DEFAULT_NIGHT_FACTOR
    night_start: time = field(default_factory=lambda: _parse_time(None, DEFAULT_NIGHT_START))
    night_end: time = field(default_factory=lambda: _parse_time(None, DEFAULT_NIGHT_END))
    ignition_delay: timedelta = field(default_factory=lambda: timedelta(minutes=DEFAULT_IGNITION_DELAY))
    safety_margin: timedelta = field(default_factory=lambda: timedelta(minutes=DEFAULT_SAFETY_MARGIN))
    window_start: time = field(default_factory=lambda: _parse_time(None, DEFAULT_RELIGHT_WINDOW_START))
    window_end: time = field(default_factory=lambda: _parse_time(None, DEFAULT_RELIGHT_WINDOW_END))
    weather_entity: str = ""

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> "_Settings":
        return cls(
            volume=float(option(entry, CONF_BUFFER_VOLUME, DEFAULT_BUFFER_VOLUME)),
            threshold=float(option(entry, CONF_BUFFER_THRESHOLD, DEFAULT_BUFFER_THRESHOLD)),
            threshold_entity=option(entry, CONF_BUFFER_THRESHOLD_ENTITY, "") or "",
            threshold_offset=float(
                option(entry, CONF_BUFFER_THRESHOLD_OFFSET, DEFAULT_BUFFER_THRESHOLD_OFFSET)
            ),
            stratification=float(
                option(entry, CONF_STRATIFICATION_FACTOR, DEFAULT_STRATIFICATION_FACTOR)
            ),
            loss_coefficient=float(option(entry, CONF_BUILDING_LOSS, DEFAULT_BUILDING_LOSS)),
            indoor_target=float(option(entry, CONF_INDOOR_TARGET, DEFAULT_INDOOR_TARGET)),
            dhw_power_kw=float(option(entry, CONF_DHW_DAILY, DEFAULT_DHW_DAILY)) / 24.0,
            night_factor=float(option(entry, CONF_NIGHT_FACTOR, DEFAULT_NIGHT_FACTOR)),
            night_start=_parse_time(option(entry, CONF_NIGHT_START, None), DEFAULT_NIGHT_START),
            night_end=_parse_time(option(entry, CONF_NIGHT_END, None), DEFAULT_NIGHT_END),
            ignition_delay=timedelta(
                minutes=float(option(entry, CONF_IGNITION_DELAY, DEFAULT_IGNITION_DELAY))
            ),
            safety_margin=timedelta(
                minutes=float(option(entry, CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN))
            ),
            window_start=_parse_time(
                option(entry, CONF_RELIGHT_WINDOW_START, None), DEFAULT_RELIGHT_WINDOW_START
            ),
            window_end=_parse_time(
                option(entry, CONF_RELIGHT_WINDOW_END, None), DEFAULT_RELIGHT_WINDOW_END
            ),
            weather_entity=option(entry, CONF_WEATHER_ENTITY, "") or "",
        )


class BufferPredictionCalculator:
    """Runs the prediction for one boiler and hands the result to its entities."""

    def __init__(self, hass: HomeAssistant, device, entry: ConfigEntry) -> None:
        self.hass = hass
        self.device = device
        self.serial = device["serial"]
        self.settings = _Settings.from_entry(entry)
        self.result = BufferPrediction()
        self._listeners: list = []
        self._lock = asyncio.Lock()
        self._last_run = 0.0
        self._last_values: dict[str, float] = {}
        self._samples: list[_Sample] = []
        self._last_charging_seen: float | None = None
        self._forecast: list[tuple[datetime, float]] = []
        self._forecast_fetched = 0.0
        self._forecast_failed = False

    # ------------------------------------------------------------------ wiring

    @callback
    def async_add_listener(self, update_callback) -> callable:
        """Subscribe an entity; the returned callable unsubscribes it."""
        self._listeners.append(update_callback)

        @callback
        def remove() -> None:
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return remove

    def async_start(self) -> callable:
        """Start the timers and the subscriptions; returns the teardown callable."""
        unsubscribes = [
            async_track_time_interval(
                self.hass, self._async_timer, timedelta(seconds=PREDICTION_INTERVAL)
            )
        ]

        # The first values are still "?" while the entry is being set up.
        unsubscribes.append(
            async_call_later(self.hass, PREDICTION_INITIAL_DELAY, self._async_timer)
        )

        if self.settings.weather_entity:
            unsubscribes.append(
                async_track_state_change_event(
                    self.hass, [self.settings.weather_entity], self._async_weather_changed
                )
            )

        tag = f"prediction_{self.serial}"
        watched = [
            self.device.get_parameter(name)
            for name in WATCHED_FOR_EVENTS
            if self.device.has_parameter(name)
        ]
        for parameter in watched:
            parameter.set_update_callback(self._async_parameter_updated, tag)

        def teardown() -> None:
            for unsubscribe in unsubscribes:
                unsubscribe()
            for parameter in watched:
                parameter.set_update_callback(None, tag)
            self._listeners.clear()

        return teardown

    # ------------------------------------------------------------------ triggers

    async def _async_timer(self, _now=None) -> None:
        await self.async_recalculate()

    @callback
    def _async_weather_changed(self, _event) -> None:
        self._forecast_fetched = 0.0  # the cached forecast is stale
        if self._debounced():
            self.hass.async_create_task(self.async_recalculate())

    async def _async_parameter_updated(self, parameter) -> None:
        """Called on every pushed value of a watched parameter.

        Deliberately cheap: a boiler pushes continuously, so a run only happens when
        a value really moved and the previous run is old enough.
        """
        name = parameter["name"]
        try:
            value = float(parameter["value"])
        except (TypeError, ValueError):
            return

        previous = self._last_values.get(name)
        if name == PARAM_BOILER_PUMP:
            # A pump starting or stopping changes the charging state: react at once.
            significant = previous is None or previous != value
        else:
            significant = (
                previous is None or abs(value - previous) >= PREDICTION_TEMPERATURE_DELTA
            )
        if not significant or not self._debounced():
            return
        await self.async_recalculate()

    def _debounced(self) -> bool:
        return time_module.monotonic() - self._last_run >= PREDICTION_MIN_INTERVAL

    # ------------------------------------------------------------------ the run

    async def async_recalculate(self, _now=None) -> None:
        """Recompute the prediction and tell the entities."""
        if self._lock.locked():
            return
        async with self._lock:
            self._last_run = time_module.monotonic()
            try:
                self.result = await self._async_build()
            except Exception:  # a bad prediction must not break the entry
                _LOGGER.exception("Buffer prediction failed for %s", self.serial)
                self.result = BufferPrediction(
                    status=STATUS_UNAVAILABLE, last_calculated=dt_util.utcnow()
                )
            for update_callback in list(self._listeners):
                update_callback()

    async def _async_build(self) -> BufferPrediction:
        now = dt_util.utcnow()
        settings = self.settings

        top = _number(self.device, PARAM_BUFFER_TOP)
        bottom = _number(self.device, PARAM_BUFFER_BOTTOM)
        if top is None or bottom is None:
            return BufferPrediction(
                status=STATUS_UNAVAILABLE,
                unavailable_reason=REASON_NO_BUFFER,
                last_calculated=now,
            )
        if settings.volume <= 0 or settings.loss_coefficient <= 0:
            return BufferPrediction(
                status=STATUS_UNAVAILABLE,
                unavailable_reason=REASON_INVALID_CONFIG,
                last_calculated=now,
            )

        mean = model.buffer_mean_temperature(top, bottom, settings.stratification)
        self._remember(now, mean)

        threshold, threshold_source = self._threshold()
        energy = model.stored_energy_kwh(
            settings.volume, top, bottom, threshold, settings.stratification
        )

        probe = _number(self.device, PARAM_OUTDOOR)
        if probe is not None and not -40.0 <= probe <= 60.0:
            probe = None
        forecast = await self._async_forecast()
        outdoor_source = (
            "boiler probe" if probe is not None else "weather forecast" if forecast else "none"
        )

        charging = self._charging(top)
        projection = model.project_depletion(
            energy,
            now,
            forecast,
            probe,
            settings.loss_coefficient,
            settings.indoor_target,
            settings.night_factor,
            settings.night_start,
            settings.night_end,
            settings.dhw_power_kw,
            heating_off=self._heating_off(probe),
            to_local=dt_util.as_local,
        )

        common = {
            "stored_energy_kwh": energy,
            "estimated_power_kw": projection.power_now_kw,
            "observed_power_kw": self._observed_power(mean),
            "buffer_mean_temperature": mean,
            "threshold_temperature": threshold,
            "threshold_source": threshold_source,
            "outdoor_temperature": probe if probe is not None else model.interpolate_forecast(forecast, now),
            "outdoor_temperature_source": outdoor_source,
            "weather_fallback": not forecast,
            "charging": charging,
            "last_calculated": now,
        }

        if charging:
            # The tank is gaining energy: projecting a decay would publish an absurd
            # relight time that corrects itself a minute later, after notifying.
            return BufferPrediction(
                status=STATUS_CHARGING,
                autonomy_hours=self._autonomy(now, projection.depletion),
                depletion_time=projection.depletion,
                **common,
            )

        if projection.beyond_horizon or projection.depletion is None:
            return BufferPrediction(
                status=STATUS_BEYOND_HORIZON,
                autonomy_hours=model.PROJECTION_HORIZON.total_seconds() / 3600.0,
                **common,
            )

        # The window is a time of day the user can act at, so it is evaluated in
        # local time: 09:20 UTC is the middle of the night in some time zones.
        raw = projection.depletion - settings.ignition_delay - settings.safety_margin
        relight = model.apply_relight_window(
            dt_util.as_local(raw),
            dt_util.as_local(now),
            settings.window_start,
            settings.window_end,
        )
        return BufferPrediction(
            status=STATUS_DEPLETED if energy <= 0 else STATUS_OK,
            relight_time=relight.moment,
            raw_relight_time=relight.raw,
            window_adjusted=relight.window_adjusted,
            late=relight.late,
            autonomy_hours=self._autonomy(now, projection.depletion),
            depletion_time=projection.depletion,
            **common,
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _autonomy(now: datetime, depletion: datetime | None) -> float:
        if depletion is None:
            return model.PROJECTION_HORIZON.total_seconds() / 3600.0
        return max(0.0, round((depletion - now).total_seconds() / 3600.0, 1))

    def _threshold(self) -> tuple[float, str]:
        """The temperature below which the radiators give up.

        The flow setpoint of the heating circuit is the right quantity: it follows
        the boiler's heating curve, so it rises when it gets colder outside. A fixed
        value would be too optimistic in deep winter.
        """
        entity_id = self.settings.threshold_entity
        if entity_id:
            state = self.hass.states.get(entity_id)
            if state is not None:
                try:
                    return (
                        float(state.state) + self.settings.threshold_offset,
                        f"entity: {entity_id}",
                    )
                except (TypeError, ValueError):
                    pass
        setpoint = _number(self.device, PARAM_FLOW_TARGET)
        if setpoint is not None:
            return setpoint + self.settings.threshold_offset, f"boiler: {PARAM_FLOW_TARGET}"
        return self.settings.threshold, "fixed"

    def _charging(self, tank_top: float) -> bool:
        """Whether a fire is currently charging the tank.

        B_STATE is not used: its vocabulary is undocumented. These three physical
        signs together are reliable, where each one alone is not (the pump may run
        for frost protection, the flue stays hot after the fire dies).
        """
        pump = _is_on(self.device, PARAM_BOILER_PUMP) or _is_on(
            self.device, PARAM_BOILER_PUMP_DEMAND
        )
        boiler = _number(self.device, PARAM_BOILER_WOOD)
        if boiler is None:
            boiler = _number(self.device, PARAM_BOILER_PELLET)
        hotter = boiler is not None and boiler > tank_top + CHARGE_BOILER_OVER_TANK
        flue = _number(self.device, PARAM_FLUE_GAS)
        firebox = _number(self.device, PARAM_FIREBOX)
        burning = (flue is not None and flue > CHARGE_FLUE_GAS) or (
            firebox is not None and firebox > CHARGE_FIREBOX
        )

        now = time_module.monotonic()
        if pump and hotter and burning:
            self._last_charging_seen = now
            return True
        # Hysteresis: B_P1 cycles, and without this the alert would flicker.
        if self._last_charging_seen is not None:
            if now - self._last_charging_seen < PREDICTION_CHARGE_HYSTERESIS:
                return True
            self._last_charging_seen = None
        return False

    def _heating_off(self, probe: float | None) -> bool:
        """Mid-season pause: no circuit pump running while it is mild outside."""
        if probe is None or probe < self.settings.indoor_target:
            return False
        has_pump = self.device.has_parameter(PARAM_CIRCUIT_PUMP) or self.device.has_parameter(
            PARAM_SECOND_PUMP
        )
        if not has_pump:
            return False
        return not (
            _is_on(self.device, PARAM_CIRCUIT_PUMP) or _is_on(self.device, PARAM_SECOND_PUMP)
        )

    def _remember(self, now: datetime, mean: float) -> None:
        """Keep an hour of mean tank temperature, to derive the observed power."""
        self._samples.append(_Sample(moment=now.timestamp(), temperature=mean))
        cutoff = now.timestamp() - 3600
        self._samples = [sample for sample in self._samples if sample.moment >= cutoff]

    def _observed_power(self, mean: float) -> float | None:
        """Power actually leaving the tank; shown for tuning, never used."""
        if len(self._samples) < 2:
            return None
        oldest = self._samples[0]
        elapsed = (self._samples[-1].moment - oldest.moment) / 3600.0
        if elapsed < 0.25:  # too short a window to mean anything
            return None
        return model.observed_power_kw(
            self.settings.volume, oldest.temperature, mean, elapsed
        )

    async def _async_forecast(self) -> list[tuple[datetime, float]]:
        """The hourly forecast, cached, or an empty list when unavailable.

        An empty list is not a failure: the projection then holds the boiler's own
        outdoor probe constant, which stays roughly right over a few hours. Losing
        the weather integration must not make the relight alert disappear.
        """
        if not self.settings.weather_entity:
            return []
        age = time_module.monotonic() - self._forecast_fetched
        if self._forecast and age < FORECAST_CACHE_SECONDS:
            return self._forecast

        for forecast_type in ("hourly", "daily"):
            points = await self._async_fetch(forecast_type)
            if points:
                self._forecast = points
                self._forecast_fetched = time_module.monotonic()
                self._forecast_failed = False
                return points

        if not self._forecast_failed:
            # Only once: this runs every five minutes.
            _LOGGER.warning(
                "No usable forecast from %s, falling back to the boiler's outdoor probe",
                self.settings.weather_entity,
            )
            self._forecast_failed = True
        return []

    async def _async_fetch(self, forecast_type: str) -> list[tuple[datetime, float]]:
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": self.settings.weather_entity, "type": forecast_type},
                blocking=True,  # required by return_response
                return_response=True,
            )
        except Exception as error:  # integrations raise various things
            _LOGGER.debug("Cannot read the %s forecast: %s", forecast_type, error)
            return []

        entry = (response or {}).get(self.settings.weather_entity) or {}
        points = []
        for item in entry.get("forecast") or []:
            moment = dt_util.parse_datetime(item.get("datetime", ""))
            temperature = item.get("temperature")
            if moment is None or temperature is None:
                continue
            points.append((dt_util.as_utc(moment), float(temperature)))
        points.sort(key=lambda point: point[0])
        return points
