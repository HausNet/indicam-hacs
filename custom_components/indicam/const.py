"""Constants for the Indicator Camera integration."""

import os

DOMAIN = "indicam"
# The key to store the component config under in the global hass object
DATA_CONFIG_INDICAM = "indicam_config_data"
# Seconds to wait between turning flash on and capturing image
FLASH_DELAY_SECONDS = 60
# Measurement event
INDICAM_MEASUREMENT = "image_processing.indicam_measurement"
# Indicam service base URL
INDICAM_URL = os.environ.get("INDICAM_URL", "https://app.hausnet.io/indicam/api")
# For oil camera, scan time in seconds - default cycle time = 24 hours, Minimum = 4 hours
VERTICAL_FLOAT_DEFAULT_SCAN_SECONDS = 24*60*60
VERTICAL_FLOAT_MIN_SCAN_SECONDS = int(os.environ.get('INDICAM_MIN_SCAN', 4*60*60))
# How many times, and for how long to wait for a measurement to be made
MEASUREMENT_PROCESS_DELAYS = [1, 5, 25, 60, 90]
# Number of times to retry image capture
IMAGE_GET_RETRIES = 3
# Timeout for grabbing images
GRAB_TIMEOUT = 10

# Attributes
ATTR_GAUGE_MEASUREMENT = "gauge_measurement"
ATTR_MATCHES = "matches"
ATTR_PROCESS_TIME = "process_time"
ATTR_TOTAL_MATCHES = "total_matches"
ATTR_SUMMARY = "summary"

# Configuration keys
CONF_AUTH_KEY = "auth_key"
CONF_CLIENT_API_KEY = "client_api_key"
CONF_INDICAMS = "indicams"
CONF_CYCLE_SECONDS = "cycle_seconds"
CONF_CAMERA_ENTITY_ID = "camera_entity_id"
CONF_FLASH_ENTITY_ID = "flash_entity_id"
CONF_PATH_OUT = "path_out"
CONF_SERVICE_DEVICE = "service_device"

