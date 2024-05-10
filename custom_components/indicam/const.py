"""Constants for the Indicator Camera integration."""

import os

DOMAIN = "indicam"
# Seconds to wait between turning flash on and capturing image
FLASH_DELAY_SECONDS = 60
# Measurement event
INDICAM_MEASUREMENT = "image_processing.indicam_measurement"
# Indicam Service URL
INDICAM_URL = os.environ.get("INDICAM_URL", "https://app.hausnet.io/indicam/api")
# For oil camera - default cycle time = 24 hours, Minimum = 4 hours
OIL_CAM_CYCLE_SECONDS_DEFAULT = 60 * 60 * 24
OIL_CAM_CYCLE_SECONDS_MINIMUM = 60 * 60 * 4

# Attributes
ATTR_GAUGE_MEASUREMENT = "gauge_measurement"
ATTR_MATCHES = "matches"
ATTR_PROCESS_TIME = "process_time"
ATTR_TOTAL_MATCHES = "total_matches"
ATTR_SUMMARY = "summary"

# Configuration keys
CONF_ADD_ANOTHER = "add_another"
CONF_AUTH_KEY = "auth_key"
CONF_INDICAMS = "indicams"
CONF_CYCLE_SECONDS = "cycle_seconds"
CONF_CAMERA_ENTITY_ID = "camera_entity_id"
CONF_FLASH_ENTITY_ID = "flash_entity_id"
CONF_PATH_OUT = "path_out"
