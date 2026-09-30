DOMAIN = "centrometal_boiler"

WEB_BOILER_CLIENT = "web_boiler_client"
WEB_BOILER_SYSTEM = "web_boiler_system"
WEB_BOILER_UNSUBSCRIBE = "web_boiler_unsubscribe"

CONF_PRODUCT_PREFIX = "product_prefix"

WEB_BOILER_LOGIN_RETRY_INTERVAL = 60
WEB_BOILER_REFRESH_INTERVAL = 600

# Centrometal device type of the BioTec-Plus (also sold as Morvan GMX EASY),
# the only boiler this integration supports.
SUPPORTED_DEVICE_TYPE = "biopl"


def connectivity_signal(username: str) -> str:
    """Dispatcher signal sent when the account's WebSocket connects or disconnects."""
    return f"{DOMAIN}_connectivity_{username}"
