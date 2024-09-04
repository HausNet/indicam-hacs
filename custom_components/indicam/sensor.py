""" IndiCam-based sensor(s)"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
import logging
from enum import StrEnum
from typing import Any

import indicam_client

from homeassistant.config_entries import ConfigEntry
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import (
    CONF_MAXIMUM,
    CONF_MINIMUM,
    CONF_NAME,
    CONF_SENSOR_TYPE,
    PERCENTAGE,
    CONF_SENSORS,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .indicator_processors import VerticalFloatProcessor, VerticalFloatDecorator, ImageGrabber
from . import IndiCamComponentState
from .const import (
    ATTR_GAUGE_MEASUREMENT,
    CONF_CAMERA_ENTITY_ID,
    CONF_FLASH_ENTITY_ID,
    CONF_SERVICE_DEVICE, DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class SensorType(StrEnum):
    """The different sensor types -- only the vertical float sensor for now."""
    VERTICAL_FLOAT = "vertical_float"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """ Set up a sensor entity from a config entry.

        Note: Allows for many sensors, but only one can be configured for now.
    """
    entities: list[IndiCamSensorEntity] = []
    for sensor_conf in entry.data[CONF_SENSORS]:
        if CONF_SENSOR_TYPE not in sensor_conf or sensor_conf[CONF_SENSOR_TYPE] != SensorType.VERTICAL_FLOAT.value:
            raise ValueError("Sensor type is not vertical float")
        name = sensor_conf[CONF_NAME]
        service_device = sensor_conf[CONF_SERVICE_DEVICE]
        camera_entity_id = sensor_conf[CONF_CAMERA_ENTITY_ID]
        sensor_options = entry.options[sensor_conf[CONF_SERVICE_DEVICE]]
        cam_config = indicam_client.CamConfig(
            min_perc=sensor_options.get(CONF_MINIMUM), max_perc=sensor_options.get(CONF_MAXIMUM)
        )
        flash_entity_id = sensor_conf[CONF_FLASH_ENTITY_ID]
        component_state: IndiCamComponentState = entry.runtime_data
        processor = VerticalFloatProcessor(hass, entry.runtime_data.api_client, service_device, cam_config)
        decorator = VerticalFloatDecorator(component_state.out_path, service_device)
        grabber = ImageGrabber(hass, camera_entity_id, flash_entity_id)
        entity = IndiCamSensorEntity(name, sensor_options[CONF_SCAN_INTERVAL], grabber, processor, decorator)
        entities.append(entity)
    async_add_entities(entities)


class IndiCamSensorEntity(SensorEntity):
    """ An entity representing a measurement extraction service operating on a camera. """

    def __init__(
            self,
            name: str,
            scan_interval_hours: float,
            grabber: ImageGrabber,
            processor: VerticalFloatProcessor,
            decorator: VerticalFloatDecorator
    ) -> None:
        """Initialize a processor entity for a camera."""
        self._name = name
        self._scan_interval_hours = scan_interval_hours
        self._grabber = grabber
        self._processor = processor
        self._decorator = decorator
        self._last_result: indicam_client.GaugeMeasurement | None = None
        self._last_time: dt.datetime | None = None

    @property
    def name(self) -> str:
        """Return the device name"""
        return self._name

    @property
    def unique_id(self) -> str | None:
        return f"{DOMAIN}{self._name}-{self._processor.device_name}"

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
        now = dt.datetime.now()
        if self._last_time is None:
            next_time = now
        else:
            next_time = self._last_time + dt. timedelta(hours=self._scan_interval_hours)
        if now < next_time:
            return
        self._last_time = now
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
