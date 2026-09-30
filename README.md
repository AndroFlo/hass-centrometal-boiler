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

Both can be changed later through the **Configure** button of the integration, without having to delete and recreate it.

If your Centrometal password changes, Home Assistant asks you to enter the new one instead of leaving the integration in a broken state.

## Supported devices

Only the **BioTec-Plus** (also Morvan GMX EASY, Centrometal type `biopl`) is supported.
Other boilers of the account are ignored (a warning is logged at startup). The PelTec, Compact,
CM Pelet-set and BioTec-L support of the upstream integration was removed; use
[9a4gl/hass-centrometal-boiler](https://github.com/9a4gl/hass-centrometal-boiler) for those boilers.

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
