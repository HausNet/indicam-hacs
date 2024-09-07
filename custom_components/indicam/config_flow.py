"""Config flow for Indicam."""
import os
from types import MappingProxyType
from typing import Optional, Any

import aiofiles.os as aio_os
import aiohttp
import indicam_client
import voluptuous as vol

from homeassistant.core import callback
from homeassistant.config_entries import ConfigFlow, ConfigEntry, OptionsFlowWithConfigEntry
from homeassistant.components.camera import DOMAIN as DOMAIN_CAMERA
from homeassistant.components.switch import DOMAIN as DOMAIN_SWITCH
from homeassistant.const import (
    CONF_MAXIMUM,
    CONF_MINIMUM,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_SENSOR_TYPE,
    CONF_PLATFORM, CONF_SENSORS,
)
from homeassistant.exceptions import ConfigEntryError, ConfigEntryAuthFailed
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    EntitySelector,
    EntitySelectorConfig, SelectOptionDict, SelectSelectorMode,
)

from .const import (
    DOMAIN,
    CONF_CLIENT_API_KEY,
    CONF_PATH_OUT,
    VERTICAL_FLOAT_DEFAULT_SCAN_HOURS,
    VERTICAL_FLOAT_MIN_SCAN_HOURS,
    CONF_SERVICE_DEVICE,
    CONF_CAMERA_ENTITY_ID,
    CONF_FLASH_ENTITY_ID, INDICAM_URL, VERTICAL_FLOAT_MAX_SCAN_HOURS
)
from .sensor import SensorType
from . import test_client_connect

# TODO: Remove default key
INTEGRATION_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_API_KEY): str,
        vol.Optional(CONF_PATH_OUT, default="/tmp/indicam"): str,
    }
)

# The base sensor platform configuration schema. There is only one platform now - a sensor.
# TODO: Remove default values & entity lists
CAMERA_ENTITY_SELECTOR = EntitySelector(EntitySelectorConfig(domain=DOMAIN_CAMERA))
FLASH_ENTITY_SELECTOR = EntitySelector(EntitySelectorConfig(domain=DOMAIN_SWITCH))
INDICAM_SENSOR_SCHEMA = vol.Schema(
    {
        # The nome of the sensor identifying it in Home Assistant
        vol.Required(CONF_NAME): str,
        # The device name representing this sensor at the service. Used to name saved images too.
        vol.Required(CONF_SERVICE_DEVICE): str,
        # The entity ID of the camera to get input images from
        vol.Required(CONF_CAMERA_ENTITY_ID): CAMERA_ENTITY_SELECTOR,
        # The entity ID of a switch controlling a flash (e.g. on-board LED)
        vol.Optional(CONF_FLASH_ENTITY_ID): FLASH_ENTITY_SELECTOR,
    }
)

# Additional configuration for vertical float sensors only.
# noinspection PyTypeChecker
SENSOR_TYPE_SELECTOR = SelectSelector(SelectSelectorConfig(
    options=[SelectOptionDict(
        value=SensorType.VERTICAL_FLOAT.value, label=SensorType.VERTICAL_FLOAT.name
    ), ],
    mode=SelectSelectorMode(SelectSelectorMode.DROPDOWN)
))
MIN_FACTOR_SELECTOR = NumberSelector(NumberSelectorConfig(
    min=0, max=25, mode=NumberSelectorMode.BOX, unit_of_measurement="%",  step=1
))
MAX_FACTOR_SELECTOR = NumberSelector(NumberSelectorConfig(
    min=0, max=25, mode=NumberSelectorMode.BOX, unit_of_measurement="%",  step=1
))
SCAN_INTERVAL_SELECTOR = NumberSelector(NumberSelectorConfig(
    min=VERTICAL_FLOAT_MIN_SCAN_HOURS,
    max=VERTICAL_FLOAT_MAX_SCAN_HOURS,
    mode=NumberSelectorMode.BOX,
    unit_of_measurement='hours',
    step='any'
))


class IndiCamConfigFlow(ConfigFlow, domain=DOMAIN):
    """Indicam config flow."""

    # The schema version of the entries
    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the input data store."""
        self.data: dict[str: str] = {}

    async def async_step_user(self, user_input=None):
        """ Handle a flow initiated by the user. """
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self.user_step_input_is_valid(user_input)
            except ConfigEntryError:
                errors["base"] = "connect"
            except ConfigEntryAuthFailed:
                errors["base"] = "api_auth"
            except PermissionError:
                errors["base"] = "permission"
            except ValueError as e:
                errors["base"] = e.args[0]
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                self.data = user_input
                self.data[CONF_SENSORS] = []
                return await self.async_step_sensor()
        return self.async_show_form(step_id="user", data_schema=INTEGRATION_CONFIG_SCHEMA, errors=errors)

    async def user_step_input_is_valid(self, user_input):
        """ Test that the input at the first step is valid.

            Executes the following tests:
                - Tries to connect to the service given the auth token
                - If a local image storage location is given, verify that it is writeable
        """
        async with aiohttp.ClientSession() as session:
            client = indicam_client.IndiCamServiceClient(session, INDICAM_URL, user_input[CONF_CLIENT_API_KEY])
            await test_client_connect(client)
        conf_path = user_input[CONF_PATH_OUT]
        if conf_path:
            allowed_match: Optional[str] = None
            for path in self.hass.config.allowlist_external_dirs:
                if conf_path.startswith(path):
                    allowed_match = path
                    break
            if not allowed_match:
                raise ValueError("not_allowed")
            if not await aio_os.access(allowed_match, os.R_OK | os.W_OK):
                raise PermissionError

    async def async_step_sensor(self, user_input=None):
        """ Allow the user to define a sensor.

            The config structure allows for future expansion to more sensors, but, only one is allowed right now.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self.sensor_step_input_is_valid(user_input)
            except ValueError:
                errors["base"] = "unknown_service_device"
            else:
                self.data[CONF_SENSORS].append({
                    CONF_PLATFORM: DOMAIN,
                    CONF_SENSOR_TYPE: SensorType.VERTICAL_FLOAT.value,
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_SERVICE_DEVICE: user_input[CONF_SERVICE_DEVICE],
                    CONF_CAMERA_ENTITY_ID: user_input[CONF_CAMERA_ENTITY_ID],
                    CONF_FLASH_ENTITY_ID: user_input[CONF_FLASH_ENTITY_ID],
                })
                # These are defaults for options
                options = {
                    user_input[CONF_SERVICE_DEVICE]: {
                        CONF_MINIMUM: 0,
                        CONF_MAXIMUM: 0,
                        CONF_SCAN_INTERVAL: VERTICAL_FLOAT_DEFAULT_SCAN_HOURS
                    }
                }
                return self.async_create_entry(title="Indicam Sensor", data=self.data, options=options)
        return self.async_show_form(step_id="sensor", data_schema=INDICAM_SENSOR_SCHEMA, errors=errors)

    async def sensor_step_input_is_valid(self, user_input) -> bool:
        """ Validate the sensor user input.

            Queries for the existence of the device at the service.
        """
        async with aiohttp.ClientSession() as session:
            client = indicam_client.IndiCamServiceClient(session, INDICAM_URL, self.data[CONF_CLIENT_API_KEY])
            if await client.get_indicam_id(user_input[CONF_SERVICE_DEVICE]) is None:
                raise ValueError("Unknown service device")
        return True

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        """ Return the options flow for this handler. """
        return IndiCamOptionsFlow(config_entry)


class IndiCamOptionsFlow(OptionsFlowWithConfigEntry):
    """ Options for the IndiCam sensor. """

    async def async_step_init(self, user_input: [str, Any] = None):
        """ Options form and validation.

            NOTE: We only deal with one sensor for now.
        """
        errors: [str, Any] = None
        if user_input is not None:
            data = {key: value for key, value in self.config_entry.data.items()}
            service_name = self.config_entry.data[CONF_SENSORS][0][CONF_SERVICE_DEVICE]
            data[service_name] = self.user_data_to_options(user_input)
            return self.async_create_entry(title="", data=data)
        return self.async_show_form(step_id="init", data_schema=self.options_schema(), errors=errors)

    def options_schema(self) -> vol.Schema:
        """ Create the options schema with defaults from the current entry. """
        service_device: str = self.config_entry.data[CONF_SENSORS][0][CONF_SERVICE_DEVICE]
        user_data = self.options_to_user_data(self.config_entry.options[service_device])
        return vol.Schema(
            {
                # Camera configuration - mark the measurement range relative to the body of the sensor
                vol.Optional(CONF_MINIMUM, default=user_data[CONF_MINIMUM]): MIN_FACTOR_SELECTOR,
                vol.Optional(CONF_MAXIMUM, default=user_data[CONF_MAXIMUM]): MAX_FACTOR_SELECTOR,
                # Overwrite the base config scan interval to add a minimum and default
                vol.Optional(CONF_SCAN_INTERVAL, default=user_data[CONF_SCAN_INTERVAL]): SCAN_INTERVAL_SELECTOR
            }
        )

    @staticmethod
    def options_to_user_data(options: MappingProxyType[str, Any]) -> dict[str, Any]:
        """ Return the user data as presented on the screen (% offsets), translated from the stored values
            (factor offsets). Limit percentage to 2 decimals (or factor to 4) to avoid rounding issues.
        """
        return {
            CONF_MINIMUM: round(options.get(CONF_MINIMUM) * 100, 2),
            CONF_MAXIMUM: round(options.get(CONF_MAXIMUM) * 100, 2),
            CONF_SCAN_INTERVAL: options.get(CONF_SCAN_INTERVAL)
        }

    @staticmethod
    def user_data_to_options(user_data: dict[str, Any]) -> dict[str, Any]:
        """ Convert data as presented to the user (% min/max), to factor values. Allow 4 decimals of accuracy
            to avoid rounding issues.
        """
        return {
            CONF_MINIMUM: round(user_data[CONF_MINIMUM] / 100, 4),
            CONF_MAXIMUM: round(user_data[CONF_MAXIMUM] / 100, 4),
            CONF_SCAN_INTERVAL: user_data[CONF_SCAN_INTERVAL]
        }
