""" IndiCam-based sensor(s)"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
import logging
from enum import StrEnum
from typing import Any

import voluptuous as vol
import indicam_client

from homeassistant.helpers.typing import UndefinedType
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.camera import DOMAIN as DOMAIN_CAMERA
from homeassistant.components.switch import DOMAIN as DOMAIN_SWITCH
from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.const import (
    CONF_MAXIMUM,
    CONF_MINIMUM,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_SENSOR_TYPE,
    PERCENTAGE,
)
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import get_component_state
from .indicator_processors import VerticalFloatProcessor, VerticalFloatDecorator, ImageGrabber

from .const import (
    ATTR_GAUGE_MEASUREMENT,
    CONF_CAMERA_ENTITY_ID,
    CONF_FLASH_ENTITY_ID,
    CONF_SERVICE_DEVICE,
    VERTICAL_FLOAT_DEFAULT_SCAN_SECONDS,
    VERTICAL_FLOAT_MIN_SCAN_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class SensorType(StrEnum):
    """The different sensor types -- only the vertical float sensor for now."""
    VERTICAL_FLOAT = "vertical_float"


# Additional configuration for vertical float sensors only.
VERTICAL_FLOAT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SENSOR_TYPE): vol.Equal(SensorType.VERTICAL_FLOAT.value),
        # Camera configuration - mark the measurement range relative to the body of the sensor
        vol.Optional(CONF_MINIMUM, default=0): cv.positive_float,
        vol.Optional(CONF_MAXIMUM, default=0): cv.positive_float,
        # Overwrite the base config scan interval to add a minimum and default
        vol.Optional(CONF_SCAN_INTERVAL, default=datetime.timedelta(seconds=VERTICAL_FLOAT_DEFAULT_SCAN_SECONDS)):
            vol.All(
                cv.time_period,
                cv.positive_timedelta,
                vol.Range(min=datetime.timedelta(seconds=VERTICAL_FLOAT_MIN_SCAN_SECONDS))
            ),
    }
)

##
# The platform configuration schema. There is only one sensor type - the vertical float, so this is the only
# configuration possible. When other types of sensors are added, the different schemas should be wrapped in
# vol.Any(), with each schema having a vol.In([SENSOR_TYPE]) to key the sub-schema.
#
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        # The nome of the sensor identifying it in Home Assistant
        vol.Required(CONF_NAME): cv.string,
        # The device name representing this sensor at the service. Used to name saved images too.
        vol.Required(CONF_SERVICE_DEVICE): cv.string,
        # The entity ID of the camera to get input images from
        vol.Required(CONF_CAMERA_ENTITY_ID): cv.entity_domain(DOMAIN_CAMERA),
        # The entity ID of a switch controlling a flash (e.g. on-board LED)
        vol.Optional(CONF_FLASH_ENTITY_ID): cv.entity_domain(DOMAIN_SWITCH),
    }
).extend(VERTICAL_FLOAT_SCHEMA.schema)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """ Set up an IndiCam sensor."""
    if CONF_SENSOR_TYPE not in config or config[CONF_SENSOR_TYPE] != SensorType.VERTICAL_FLOAT:
        _LOGGER.error("Sensor type is not vertical float")
        return
    name = config[CONF_NAME]
    service_device = config[CONF_SERVICE_DEVICE]
    camera_entity_id = config[CONF_CAMERA_ENTITY_ID]
    cam_config = indicam_client.CamConfig(
        min_perc=config[CONF_MINIMUM], max_perc=config[CONF_MAXIMUM]
    )
    flash_entity_id = config.get(CONF_FLASH_ENTITY_ID, None)
    processor = VerticalFloatProcessor(hass, get_component_state().api_client, service_device, cam_config)
    decorator = VerticalFloatDecorator(hass, get_component_state().out_path, service_device)
    grabber = ImageGrabber(hass, camera_entity_id, flash_entity_id)
    entity = IndiCamSensorEntity(name, grabber, processor, decorator)
    async_add_entities([entity,])


class IndiCamSensorEntity(SensorEntity):
    """ An entity representing a measurement extraction service operating on a camera. """

    def __init__(
            self,
            name: str,
            grabber: ImageGrabber,
            processor: VerticalFloatProcessor,
            decorator: VerticalFloatDecorator
    ) -> None:
        """Initialize a processor entity for a camera."""
        self._name = name
        self._grabber = grabber
        self._processor = processor
        self._decorator = decorator
        self._last_result: indicam_client.GaugeMeasurement | None = None

    @property
    def name(self) -> str | UndefinedType | None:
        """Return the device name"""
        return self._name

    @property
    def native_unit_of_measurement(self) -> float:
        """Return percentage as the unit of measurement"""
        return PERCENTAGE

    @property
    def device_class(self) -> SensorDeviceClass:
        """The device class -- volume"""
        return SensorDeviceClass.VOLUME_STORAGE

    @property
    def state_class(self) -> SensorStateClass:
        """The state class -- a measurement """
        return SensorStateClass.MEASUREMENT

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return device specific state attributes"""
        return {ATTR_GAUGE_MEASUREMENT: self._last_result}

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor"""
        if self._last_result is None:
            return None
        return round(self._last_result.value * 100, 1)

    async def async_update(self) -> None:
        """ Takes and saves a snapshot image, and processes it to obtain a measurement.
            If a result was obtained, creates a decorated image showing the measurements, and saves it.
        """
        _LOGGER.info("Grabbing vertical float snapshot image")
        image: bytes = await self._grabber.grab_image()
        if not image:
            _LOGGER.error("No image captured, skipping processing step")
            return
        measurement, elapsed = await self._processor.process_img(image)
        _LOGGER.info("Image processing time: %f", elapsed)
        failed = not measurement or not measurement.value
        if failed:
            _LOGGER.error("Measurement extraction failed, keeping last measurement as state")
            return
        self._last_result = measurement
        # Save the decorated image showing the measurements
        await self._decorator.decorate_and_save(image, self._last_result, self._processor.cam_config)
