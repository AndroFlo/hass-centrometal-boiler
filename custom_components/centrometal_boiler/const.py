DOMAIN = "centrometal_boiler"

WEB_BOILER_CLIENT = "web_boiler_client"
WEB_BOILER_SYSTEM = "web_boiler_system"
WEB_BOILER_UNSUBSCRIBE = "web_boiler_unsubscribe"
WEB_BOILER_PREDICTION = "web_boiler_prediction"

CONF_PRODUCT_PREFIX = "product_prefix"

# Buffer tank relight prediction (opt-in, see prediction.py)
CONF_PREDICTION_ENABLED = "prediction_enabled"
CONF_BUFFER_VOLUME = "buffer_volume"
CONF_BUFFER_THRESHOLD_ENTITY = "buffer_threshold_entity"
CONF_BUFFER_THRESHOLD = "buffer_threshold"
CONF_BUFFER_THRESHOLD_OFFSET = "buffer_threshold_offset"
CONF_STRATIFICATION_FACTOR = "stratification_factor"
CONF_BUILDING_LOSS = "building_loss_coefficient"
CONF_INDOOR_TARGET = "indoor_target_temperature"
CONF_DHW_DAILY = "dhw_daily_energy"
CONF_NIGHT_FACTOR = "night_reduction_factor"
CONF_NIGHT_START = "night_start"
CONF_NIGHT_END = "night_end"
CONF_IGNITION_DELAY = "ignition_delay"
CONF_SAFETY_MARGIN = "safety_margin"
CONF_RELIGHT_WINDOW_START = "relight_window_start"
CONF_RELIGHT_WINDOW_END = "relight_window_end"
CONF_WEATHER_ENTITY = "weather_entity"

# Off by default: most installations have no buffer tank, and the model needs the
# loss coefficient to be tuned before it says anything useful.
DEFAULT_PREDICTION_ENABLED = False
DEFAULT_BUFFER_VOLUME = 1000
DEFAULT_BUFFER_THRESHOLD = 40.0
# A mixing valve cannot deliver a 45 C flow out of a 45 C tank.
DEFAULT_BUFFER_THRESHOLD_OFFSET = 3.0
DEFAULT_STRATIFICATION_FACTOR = 0.5
DEFAULT_BUILDING_LOSS = 0.30
DEFAULT_INDOOR_TARGET = 20.0
DEFAULT_DHW_DAILY = 3.0
DEFAULT_NIGHT_FACTOR = 0.8
DEFAULT_NIGHT_START = "22:00:00"
DEFAULT_NIGHT_END = "06:00:00"
DEFAULT_IGNITION_DELAY = 25
DEFAULT_SAFETY_MARGIN = 30
DEFAULT_RELIGHT_WINDOW_START = "07:00:00"
DEFAULT_RELIGHT_WINDOW_END = "22:00:00"

# The tank has a time constant of hours: recomputing every five minutes is plenty.
PREDICTION_INTERVAL = 300
# Parameters are pushed continuously, so an event-driven recomputation is debounced.
PREDICTION_MIN_INTERVAL = 60
PREDICTION_TEMPERATURE_DELTA = 0.5
# The values are still "?" when the entry is set up.
PREDICTION_INITIAL_DELAY = 10
# B_P1 cycles; without this the alert would flicker when a fire dies out.
PREDICTION_CHARGE_HYSTERESIS = 1200
FORECAST_CACHE_SECONDS = 900

WEB_BOILER_LOGIN_RETRY_INTERVAL = 60
WEB_BOILER_REFRESH_INTERVAL = 600

# Centrometal device type of the BioTec-Plus (also sold as Morvan GMX EASY),
# the only boiler this integration supports.
SUPPORTED_DEVICE_TYPE = "biopl"


def connectivity_signal(username: str) -> str:
    """Dispatcher signal sent when the account's WebSocket connects or disconnects."""
    return f"{DOMAIN}_connectivity_{username}"
