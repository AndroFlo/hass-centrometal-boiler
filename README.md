[![Validate](https://github.com/AndroFlo/hass-centrometal-boiler/actions/workflows/validate.yml/badge.svg)](https://github.com/AndroFlo/hass-centrometal-boiler/actions/workflows/validate.yml)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

# hass-centrometal-boiler

Home Assistant custom component integration for the Centrometal **BioTec-Plus** boiler (also sold as **Morvan GMX EASY**) with a CM WiFi-Box.

To visualize the boiler display as a card, use the [lovelace-centrometal-boiler-card](https://github.com/AndroFlo/lovelace-centrometal-boiler-card) card.

## About

This component is based on the [py-centrometal-web-boiler](https://github.com/AndroFlo/py-centrometal-web-boiler) library (fork published on PyPI as `py-centrometal-web-boiler-androflo`), which connects to the Centrometal web boiler system.

This repository is a fork of [9a4gl/hass-centrometal-boiler](https://github.com/9a4gl/hass-centrometal-boiler), maintained at [AndroFlo/hass-centrometal-boiler](https://github.com/AndroFlo/hass-centrometal-boiler). Please report issues about this fork on [its own issue tracker](https://github.com/AndroFlo/hass-centrometal-boiler/issues).

The integration is built on an analysis of Centrometal's web application; it is not an official integration and Centrometal provides no specification or support for it.

## Installation

Requires Home Assistant **2024.11.2** or newer.

### Installation through HACS

If you have not installed HACS yet, get it at https://hacs.xyz/ and follow its installation and configuration steps.

1. In Home Assistant, open **HACS**.
2. Open the three-dot menu in the top right corner and choose **Custom repositories**.
3. Add `https://github.com/AndroFlo/hass-centrometal-boiler` as the repository and pick **Integration** as the category.
4. Search for **Centrometal Boiler System** in HACS and install it.
5. Restart Home Assistant.
6. Add the integration through *Settings -> Devices & Services -> Add Integration*.

### Manual installation

Copy the `custom_components/centrometal_boiler` folder of this repository into the `custom_components/centrometal_boiler` folder of your Home Assistant configuration, then restart Home Assistant.

Alternatively, use the following commands from an SSH shell on your Home Assistant system. Do NOT run these directly on your PC against a mounted Home Assistant file system: the resulting symlink would be broken for Home Assistant.

```
cd /config
git clone https://github.com/AndroFlo/hass-centrometal-boiler.git

# if the custom_components folder does not exist yet:
mkdir custom_components

cd custom_components
ln -s ../hass-centrometal-boiler/custom_components/centrometal_boiler
```

## Configuration

Set the integration up from *Settings -> Devices & Services*, search for "Centrometal Boiler System" and enter the e-mail address and password of your Centrometal account.

Two optional settings control how the created entities are named:

* **Prefix** — prefixes every entity created by this integration. Useful when you run several accounts side by side.
* **Prefix all sensors with the boiler's name** — includes the product name in every entity name. Enabled by default.

Both can be changed later through the **Configure** button of the integration, without having to
delete and recreate it. That button now opens a menu: *Entity naming* for the two settings above,
and *Buffer tank relight prediction* for the optional feature described below.

If your Centrometal password changes, Home Assistant asks you to enter the new one instead of leaving the integration in a broken state.

## Supported devices

Only the **BioTec-Plus** (also Morvan GMX EASY, Centrometal type `biopl`) is supported.
Other boilers of the account are ignored (a warning is logged at startup). The PelTec, Compact,
CM Pelet-set and BioTec-L support of the upstream integration was removed; use
[9a4gl/hass-centrometal-boiler](https://github.com/9a4gl/hass-centrometal-boiler) for those boilers.

## Buffer tank relight prediction

A wood boiler with a buffer tank runs in cycles: you light the fire by hand, it charges the tank,
it dies out, and the tank alone feeds the radiators until it runs dry. This optional feature
predicts **when the fire has to be lit again**, so you are told before the house goes cold rather
than after — and before you go to bed, since nobody lights a wood fire at 3 a.m.

It is **disabled by default**. Turn it on in *Configure → Buffer tank relight prediction*. The
entities are only created for boilers that actually report a buffer tank (`B_Tak1_1`).

Four entities appear (entity ids depend on the naming options):

| Entity | What it gives |
|---|---|
| `sensor.biotec_buffer_tank_relight_time` | When to light the fire, ignition delay already deducted |
| `sensor.biotec_buffer_tank_autonomy` | Hours of heating left in the tank |
| `sensor.biotec_buffer_tank_stored_energy` | Usable energy left, in kWh |
| `binary_sensor.biotec_buffer_tank_relight_alert` | On when it is time to go and light it |

A notification is one automation away:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.biotec_buffer_tank_relight_alert
    to: "on"
actions:
  - action: notify.mobile_app_phone
    data:
      message: "Time to light the stove."
```

### The model

The usable energy is the tank volume times how far it is above the temperature the heating needs:

```
energy (kWh) = volume (L) x 0.001163 x (mean tank temperature - threshold)
```

The threshold is taken from the heating circuit flow setpoint (`C1B_Tpol`) plus a small margin,
because that setpoint follows the boiler's heating curve: it rises when it gets colder outside, so
a fixed value would be too optimistic in deep winter. You can point the option at another entity,
and a fixed fallback is used when neither is available.

The heat demand is a single loss coefficient against the indoor/outdoor difference:

```
power (kW) = loss coefficient (kW/K) x (indoor target - outdoor) + hot water
```

That demand is projected forward over the weather forecast (the boiler's own outdoor probe is used
for the present and blended into the forecast over two hours) until the energy runs out. The
relight time is that moment minus the ignition delay and the safety margin, moved back into the
hours when you can actually light the fire.

### Tuning it

One number matters: the **building loss coefficient**. Everything else can stay on its default.

The sensors publish an `observed_power_kw` attribute, computed from how fast the tank actually
cools, next to the modelled `estimated_power_kw`. They are shown side by side precisely so you can
correct the coefficient yourself: if you see `observed 6.2` against `estimated 4.1`, multiply your
coefficient by about 1.5. The observed value never feeds the prediction.

As a starting point, `loss coefficient ≈ power you need on a cold day / (20 − that day's outdoor
temperature)`. With a 1000 L tank charged 20 K above the threshold (23 kWh):

| Coefficient | −5 °C | 0 °C | +5 °C | +10 °C |
|---|---|---|---|---|
| 0.15 (well insulated) | 6.0 h | 7.4 h | 9.7 h | 14.1 h |
| 0.30 (default) | 3.0 h | 3.8 h | 5.0 h | 7.3 h |
| 0.60 (older house) | 1.5 h | 1.9 h | 2.5 h | 3.8 h |

### What it does not know

This is an estimate, and it is worth knowing where it is weak:

* The tank has **two probes for a thousand litres**, so the stored energy is off by roughly ±20 %
  whatever the software does. The `stratification_factor` option lets you weight them if you see a
  consistent bias.
* The loss coefficient is yours to set, and an error on it carries straight through to the result.
* Solar gain, cooking and occupants are ignored, so a sunny day in mid-season lasts longer than
  predicted.
* The building's inertia is ignored: a swing in the forecast shows up in the prediction faster than
  the house would actually feel it.
* The tank's own standing losses (~4 %/day) are ignored, and hot water is a flat daily figure.
* The boiler's own time programme is not read; a single night setback factor approximates it.
* While a fire is burning the prediction is suspended: the tank is gaining energy, so projecting a
  decay would be meaningless.

In practice it is worth about ±1 h over six hours, ±3 h over a day. Enough to answer "do I need to
light it tonight?", not "do I light it at 18:12?".

## Controlling the boiler

The boiler and its heating circuits are exposed as `switch` entities, so they are controlled with the standard Home Assistant switch services:

```yaml
# Start the boiler
action: switch.turn_on
target:
  entity_id: switch.biotec_plus_boiler_switch

# Stop the boiler
action: switch.turn_off
target:
  entity_id: switch.biotec_plus_boiler_switch
```

A `button` entity switches the boiler from wood to pellet mode (there is no remote command back to wood):

```yaml
action: button.press
target:
  entity_id: button.biotec_plus_pellet_mode
```

The exact entity ids depend on the naming options described above.

## Development

### Debugging

To enable debug logging for this integration and its library, add the following to your Home Assistant `configuration.yaml`:

```
logger:
  default: info
  logs:
    custom_components.centrometal_boiler: debug
    centrometal_web_boiler: debug
```

After a restart, detailed log entries appear in `/config/home-assistant.log`.

Home Assistant's own "Enable debug logging" button on the integration page also turns on the library logs, thanks to the `loggers` entry in the manifest.
