"""Constants for the Paseo integration."""

from datetime import timedelta

DOMAIN = "paseo"
INTEGRATION_VERSION = "0.1.1"

CONF_URL = "url"
CONF_PASSWORD = "password"
DEFAULT_URL = "ws://localhost:6767/ws"

CONNECT_TIMEOUT_SECONDS = 15
MAX_MESSAGE_SIZE = 16 * 1024 * 1024
USAGE_REFRESH_INTERVAL = timedelta(minutes=5)

EVENT_STARTED = "started"
EVENT_FINISHED = "finished"
EVENT_NEEDS_INPUT = "needs_input"
EVENT_FAILED = "failed"
EVENT_TYPES = [EVENT_STARTED, EVENT_FINISHED, EVENT_NEEDS_INPUT, EVENT_FAILED]
