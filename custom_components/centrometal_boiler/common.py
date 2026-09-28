from homeassistant.core import HomeAssistant
from .const import DOMAIN, WEB_BOILER_CLIENT, WEB_BOILER_SYSTEM

import homeassistant.util.dt as dt_util
from datetime import datetime


def create_device_info(device) -> dict:
    param_power = device.get_parameter("B_sng")
    param_fw_ver = device.get_parameter("B_VER")
    param_wifi_ver = device.get_parameter("B_WifiVER")
    power = param_power["value"] or "?"
    firmware_ver = param_fw_ver["value"] or "?"
    wifi_ver = param_wifi_ver["value"] or "?"
    model = device["product"] + " " + power
    serial = device["serial"]
    name = "Centrometal Boiler " + model + " " + serial
    sw_version = firmware_ver + " Wifi:" + wifi_ver
    return {
        "identifiers": {
            # Serial numbers are unique identifiers within a specific domain
            (DOMAIN, device["serial"])
        },
        "name": name,
        "manufacturer": "Centrometal",
        "model": model,
        "sw_version": sw_version,
    }


def format_time(hass: HomeAssistant, timestamp, tzinfo=None):
    if tzinfo is None:
        tzinfo = dt_util.get_time_zone(hass.config.time_zone)
    # Build an aware datetime from the epoch: without tz= the value would be
    # interpreted in the host time zone, which is wrong whenever the host
    # differs from the one configured in Home Assistant (e.g. a UTC container).
    dt = datetime.fromtimestamp(timestamp, tz=dt_util.UTC)
    return dt.astimezone(tzinfo).strftime("%d.%m.%Y %H:%M:%S")


def format_name(hass: HomeAssistant, device, name) -> str:
    name = name.replace("GMX EASY", "biotec")
    username = device.username
    serial = device["serial"]
    web_boiler_client = hass.data[DOMAIN][username][WEB_BOILER_CLIENT]
    web_boiler_system = hass.data[DOMAIN][username][WEB_BOILER_SYSTEM]
    if len(web_boiler_client.data.values()) > 1:
        name = f"{serial} {name}"
    if len(web_boiler_system.prefix) > 0:
        # prefix already ends with a space (see WebBoilerSystem.__init__).
        return f"{web_boiler_system.prefix}{name}"
    return name
