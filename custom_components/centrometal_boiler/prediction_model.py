"""The physics of the buffer tank, with no Home Assistant import.

A wood boiler with a buffer tank runs in cycles: the fire is lit by hand, charges
the tank, dies out, and the tank alone feeds the radiators until it runs dry. This
module answers one question: when must the fire be lit again?

Nothing here touches Home Assistant, so it runs in tests/ in milliseconds. The
entities and the Home Assistant plumbing live in prediction.py.

The model is deliberately simple (see the README for its limits): the stored energy
is the volume times the temperature above the point where the radiators give up, and
the heat demand is a single loss coefficient times the indoor/outdoor difference.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from datetime import date, datetime, time, timedelta

# Energy of water: density x specific heat, in kWh per litre and per kelvin.
# 1000 kg/m3 x 4186 J/(kg.K) = 1.163 Wh/(L.K).
WATER_KWH_PER_LITRE_KELVIN = 1.163e-3

# The projection walks the forecast in steps, up to a horizon. Beyond it, the
# answer would be dominated by the forecast error, so we report "later than that".
PROJECTION_STEP = timedelta(minutes=15)
PROJECTION_HORIZON = timedelta(hours=48)

# The boiler's own outdoor probe is trusted for the present moment, the forecast
# for later. Blending over this period avoids a step in the projected curve.
OUTDOOR_BLEND = timedelta(hours=2)


@dataclass(frozen=True)
class Projection:
    """When the tank runs dry, and the numbers that explain it."""

    depletion: datetime | None  # None when the tank lasts beyond the horizon
    beyond_horizon: bool
    power_now_kw: float


def stored_energy_kwh(
    volume_litres: float,
    temperature_top: float,
    temperature_bottom: float,
    threshold: float,
    stratification_factor: float = 0.5,
) -> float:
    """Usable energy left in the tank, above the temperature the heating needs.

    Two probes cannot describe the stratification of a 1000 L tank, so the mean is
    weighted: stratification_factor is the share of the top probe. Energy below the
    threshold exists but cannot heat the house, so it does not count.
    """
    mean = buffer_mean_temperature(
        temperature_top, temperature_bottom, stratification_factor
    )
    usable = max(0.0, mean - threshold)
    return volume_litres * WATER_KWH_PER_LITRE_KELVIN * usable


def buffer_mean_temperature(
    temperature_top: float, temperature_bottom: float, stratification_factor: float = 0.5
) -> float:
    """Weighted mean of the two probes."""
    factor = min(1.0, max(0.0, stratification_factor))
    return factor * temperature_top + (1.0 - factor) * temperature_bottom


def heating_power_kw(
    loss_coefficient: float,
    indoor_target: float,
    outdoor: float,
    night_factor: float = 1.0,
    dhw_power_kw: float = 0.0,
) -> float:
    """Power the house draws from the tank, in kW.

    A single loss coefficient (kW/K) against the indoor/outdoor difference. Warmer
    outside than the target means no heating at all, but domestic hot water keeps
    drawing from the same tank.
    """
    deficit = max(0.0, indoor_target - outdoor)
    return loss_coefficient * deficit * night_factor + dhw_power_kw


def observed_power_kw(
    volume_litres: float,
    mean_temperature_before: float,
    mean_temperature_now: float,
    elapsed_hours: float,
    stratification_factor: float = 0.5,
) -> float | None:
    """Power actually leaving the tank, from how fast it cooled down.

    This never feeds the prediction: it is shown next to the modelled power so the
    loss coefficient can be corrected by hand. Returns None when the window is too
    short to mean anything, or when the tank warmed up (a fire is burning).
    """
    if elapsed_hours <= 0.0:
        return None
    drop = mean_temperature_before - mean_temperature_now
    if drop <= 0.0:
        return None
    return volume_litres * WATER_KWH_PER_LITRE_KELVIN * drop / elapsed_hours


def is_night(
    moment: datetime, night_start: time, night_end: time, to_local=None
) -> bool:
    """Whether the moment falls in the reduced-temperature period.

    The setback is a time of day, so it must be judged in the local time zone:
    midnight UTC is the evening or the morning depending on where you live. The
    caller passes the converter, which keeps this module free of Home Assistant.
    The period usually wraps around midnight (22:00 to 06:00), hence the two cases.
    """
    current = (to_local(moment) if to_local else moment).time()
    if night_start == night_end:
        return False
    if night_start < night_end:
        return night_start <= current < night_end
    return current >= night_start or current < night_end


def interpolate_forecast(
    forecast: list[tuple[datetime, float]], moment: datetime
) -> float | None:
    """Outdoor temperature at a moment, interpolated between hourly points.

    Before the first point and after the last one, the nearest value is held: a
    forecast that stops in 12 hours should not make the projection stop with it.
    """
    if not forecast:
        return None
    if moment <= forecast[0][0]:
        return forecast[0][1]
    if moment >= forecast[-1][0]:
        return forecast[-1][1]
    for (before, value_before), (after, value_after) in pairwise(forecast):
        if before <= moment <= after:
            span = (after - before).total_seconds()
            if span <= 0:
                return value_before
            ratio = (moment - before).total_seconds() / span
            return value_before + ratio * (value_after - value_before)
    return forecast[-1][1]


def outdoor_at(
    moment: datetime,
    now: datetime,
    forecast: list[tuple[datetime, float]],
    probe: float | None,
) -> float | None:
    """Outdoor temperature to use at a given moment of the projection.

    The boiler's probe is the truth right now; the forecast takes over later. Jumping
    straight from one to the other would put a step in the curve, so the two are
    blended over the first couple of hours.
    """
    predicted = interpolate_forecast(forecast, moment)
    if probe is None:
        return predicted
    if predicted is None:
        return probe
    elapsed = (moment - now).total_seconds()
    blend = OUTDOOR_BLEND.total_seconds()
    if elapsed <= 0:
        return probe
    if elapsed >= blend:
        return predicted
    ratio = elapsed / blend
    return probe + ratio * (predicted - probe)


def project_depletion(
    energy_kwh: float,
    now: datetime,
    forecast: list[tuple[datetime, float]],
    probe: float | None,
    loss_coefficient: float,
    indoor_target: float,
    night_factor: float,
    night_start: time,
    night_end: time,
    dhw_power_kw: float,
    heating_off: bool = False,
    to_local=None,
) -> Projection:
    """Walk the forecast forward until the usable energy is gone.

    heating_off reflects a mid-season pause (no circuit pump running while it is
    warmer outside than the target). It only applies to the current step: we cannot
    predict when the pumps will start again.
    """
    # `or indoor_target` would be wrong here: 0 degrees outside is falsy.
    outdoor_now = outdoor_at(now, now, forecast, probe)
    power_now = heating_power_kw(
        loss_coefficient,
        indoor_target,
        indoor_target if outdoor_now is None else outdoor_now,
        night_factor if is_night(now, night_start, night_end, to_local) else 1.0,
        dhw_power_kw,
    )
    if heating_off:
        power_now = dhw_power_kw

    if energy_kwh <= 0.0:
        return Projection(depletion=now, beyond_horizon=False, power_now_kw=power_now)

    remaining = energy_kwh
    moment = now
    deadline = now + PROJECTION_HORIZON
    step_hours = PROJECTION_STEP.total_seconds() / 3600.0

    while moment < deadline:
        outdoor = outdoor_at(moment, now, forecast, probe)
        if outdoor is None:
            # No forecast and no probe: nothing sensible left to project with.
            return Projection(depletion=None, beyond_horizon=True, power_now_kw=power_now)
        power = heating_power_kw(
            loss_coefficient,
            indoor_target,
            outdoor,
            night_factor if is_night(moment, night_start, night_end, to_local) else 1.0,
            dhw_power_kw,
        )
        if power <= 0.0:
            # Nothing is drawn: the tank would last forever at this rate.
            moment += PROJECTION_STEP
            continue
        remaining -= power * step_hours
        moment += PROJECTION_STEP
        if remaining <= 0.0:
            return Projection(
                depletion=moment, beyond_horizon=False, power_now_kw=power_now
            )

    return Projection(depletion=None, beyond_horizon=True, power_now_kw=power_now)


@dataclass(frozen=True)
class RelightTime:
    """When to light the fire, and whether the night window moved it."""

    moment: datetime
    raw: datetime
    window_adjusted: bool
    late: bool


def apply_relight_window(
    raw: datetime,
    now: datetime,
    window_start: time,
    window_end: time,
) -> RelightTime:
    """Move a relight time into the hours when someone can light the fire.

    A wood fire is lit by hand: telling the user to relight at 3 a.m. is useless.
    When the raw time falls outside the window, it is moved back to the last usable
    moment before the window closed, which turns "relight at 03:05" into "load the
    stove before 22:00".
    """
    if _in_window(raw, window_start, window_end):
        return RelightTime(moment=raw, raw=raw, window_adjusted=False, late=raw < now)

    moved = _previous_window_end(raw, window_end)
    if moved < now:
        # The window was missed: say so instead of publishing a silent past
        # timestamp, which would never fire a notification.
        return RelightTime(moment=now, raw=raw, window_adjusted=True, late=True)
    return RelightTime(moment=moved, raw=raw, window_adjusted=True, late=False)


def _in_window(moment: datetime, start: time, end: time) -> bool:
    """Whether a moment falls inside the allowed window (which may wrap midnight)."""
    current = moment.time()
    if start == end:
        return True
    if start < end:
        return start <= current <= end
    return current >= start or current <= end


def _previous_window_end(moment: datetime, end: time) -> datetime:
    """The last instant the window was open, strictly before the given moment."""
    candidate = _combine(moment, moment.date(), end)
    if candidate >= moment:
        candidate = _combine(moment, moment.date() - timedelta(days=1), end)
    return candidate


def _combine(reference: datetime, day: date, moment: time) -> datetime:
    """Build an aware datetime on `day` at `moment`, in the reference's time zone."""
    return datetime.combine(day, moment, tzinfo=reference.tzinfo)
