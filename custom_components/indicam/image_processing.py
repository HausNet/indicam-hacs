"""IndiCam component's image processing code.

Provides conversion of snapshots of analog sensor dials and gauges to
sensor values via the IndiCam image processing service.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
import datetime as dt
import logging
from typing import Any

import voluptuous as vol
import indicam_client

from homeassistant.components import persistent_notification
from . import setup_component_state, get_component_state

from homeassistant.components.camera import DOMAIN as DOMAIN_CAMERA, Image
from homeassistant.components.image_processing import (
    DOMAIN as DOMAIN_IMAGE_PROCESSING,
    PLATFORM_SCHEMA,
    SERVICE_SCAN,
    ImageProcessingEntity,
)
from homeassistant.components.switch import DOMAIN as DOMAIN_SWITCH
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_MAXIMUM,
    CONF_MINIMUM,
    CONF_NAME,
    EVENT_HOMEASSISTANT_STARTED,
)
from homeassistant.core import Event, HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .indicator_processors import VerticalFloatProcessor, VerticalFloatDecorator, ImageGrabber

from .const import (
    ATTR_GAUGE_MEASUREMENT,
    CONF_AUTH_KEY,
    CONF_CAMERA_ENTITY_ID,
    CONF_CYCLE_SECONDS,
    CONF_FLASH_ENTITY_ID,
    CONF_INDICAMS,
    CONF_PATH_OUT,
    INDICAM_MEASUREMENT,
    VERTICAL_FLOAT_DEFAULT_SCAN_SECONDS,
    VERTICAL_FLOAT_MIN_SCAN_SECONDS,
)

# Effectively disable automatic scanning
SCAN_INTERVAL = dt.timedelta(days=365)

_LOGGER = logging.getLogger(__name__)

INDICAM_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_CAMERA_ENTITY_ID): cv.entity_domain(DOMAIN_CAMERA),
        vol.Optional(CONF_MINIMUM, default=0): cv.positive_float,
        vol.Optional(CONF_MAXIMUM, default=0): cv.positive_float,
        vol.Optional(CONF_PATH_OUT): cv.path,
        vol.Optional(CONF_FLASH_ENTITY_ID): cv.entity_domain(DOMAIN_SWITCH),
        vol.Optional(
            CONF_CYCLE_SECONDS, default=VERTICAL_FLOAT_DEFAULT_SCAN_SECONDS
        ): vol.All(vol.Coerce(int), vol.Range(min=VERTICAL_FLOAT_MIN_SCAN_SECONDS)),
    }
)
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_AUTH_KEY): str,
        vol.Required(CONF_INDICAMS): vol.All(cv.ensure_list, [INDICAM_SCHEMA]),
    }
)


@dataclass
class IndicamMeasurement(indicam_client.GaugeMeasurement):
    """Combines measurement with entity ID for event communication."""

    entity_id: str


# noinspection PyUnusedLocal
async def async_setup_platform(
        hass: HomeAssistant,
        config: ConfigType,
        async_add_entities: AddEntitiesCallback,
        discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the IndiCam image processing platform.

       Setting up the client through the component setup is a bridge to allow the current config
       to continue working.
    """
    if not get_component_state():
        await setup_component_state(hass, config[CONF_AUTH_KEY])
        persistent_notification.async_create(
            hass,
            (
                'The IndiCam image processing platform used its own configuration for the component. '
                'Please create an Indicam component configuration instead, and switch from the image_processing '
                'platform to the <a href="https://docs.hausnet.io/indicam/#install-and-configure-the-indicam-platform">'
                'sensor platform</a>.'
            ),
            title="Please re-configure IndiCam"
        )
    entities = []
    for indicam in config[CONF_INDICAMS]:
        indicam_name = indicam[CONF_NAME]
        camera_entity_id = indicam[CONF_CAMERA_ENTITY_ID]
        cam_config = indicam_client.CamConfig(
            min_perc=indicam[CONF_MINIMUM], max_perc=indicam[CONF_MAXIMUM]
        )
        cycle_time = dt.timedelta(seconds=indicam[CONF_CYCLE_SECONDS])
        flash_entity_id = indicam.get(CONF_FLASH_ENTITY_ID, None)
        outfile_path = indicam.get(CONF_PATH_OUT, None)
        processor = VerticalFloatProcessor(hass, get_component_state().api_client, indicam_name, cam_config)
        entities.append(
            IndiCamImageProcessingEntity(
                hass,
                indicam_name,
                camera_entity_id,
                cycle_time,
                flash_entity_id,
                outfile_path,
                processor,
            )
        )

    async_add_entities(entities)

    async def start_cycles(_event: Event) -> None:
        """Start the timers.

        After startup is done, set the timers running for the indicam image
        processing.
        """
        for indicam_imgproc in entities:
            await indicam_imgproc.start_cycle()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, start_cycles)


# Simplify definition of calling a function after a delay
CallLater = Callable[[dt.datetime], Coroutine[Any, Any, None] | None]


class IndiCamImageProcessingEntity(ImageProcessingEntity):
    """Wrap a camera as an IndiCam."""

    def __init__(
            self,
            hass: HomeAssistant,
            name: str,
            camera_entity_id: str,
            cycle_time: dt.timedelta,
            flash_entity_id: str,
            outfile_path: str,
            processor: VerticalFloatProcessor
    ) -> None:
        """Initialize a processor entity for a camera."""
        self._name = name
        self._camera_entity_id = camera_entity_id
        self._cycle_time = cycle_time
        self._processor = processor
        self._last_result: indicam_client.GaugeMeasurement | None = None
        self._decorator = VerticalFloatDecorator(hass, outfile_path, name)
        self._grabber = ImageGrabber(hass, camera_entity_id, flash_entity_id)

    async def start_cycle(self):
        """Process first image, then get cycling going.

        Executes a first capture & process cycle, and then sets a repeating
        cycle timer to do this regularly.
        """

        # noinspection PyUnusedLocal
        @callback
        async def scan(scantime=None):
            """Execute a scan."""
            await self.hass.services.async_call(
                DOMAIN_IMAGE_PROCESSING, SERVICE_SCAN, {ATTR_ENTITY_ID: self.entity_id}
            )

        await scan()
        async_track_time_interval(
            self.hass,
            scan,
            self._cycle_time,
            name=f"IndiCam cycle - {self.entity_id}",
        )

    @property
    def camera_entity(self) -> str | None:
        """Return the camera entity ID."""
        return self._camera_entity_id

    @property
    def name(self) -> str:
        """Return the name of the image processor."""
        return self._name

    @property
    def state(self) -> float | str | None:
        """Return the state of the entity -- the last measurement made."""
        if not self._last_result or self._last_result.value is None:
            return None
        return self._last_result.value

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return device specific state attributes."""
        return {ATTR_GAUGE_MEASUREMENT: self._last_result}

    async def async_process_image(self, image: Image) -> None:
        """Saves the image as a snapshot, and process it. If a result was obtained,
           creates a decorated image showing the measurements, and saves it.
        """
        _LOGGER.info("Processing oil tank image")
        # Send the image for processing
        measurement, elapsed_time = await self._processor.process_img(image)
        failed = not measurement or not measurement.value
        self._last_result = None if failed else measurement
        # Save the decorated image showing the measurements
        await self._decorator.decorate_and_save(
            image, self._last_result if not failed else None, self._processor.cam_config
        )
        if failed:
            _LOGGER.error("Failed to process oil tank image")
            return
        await self._async_post_measurement_event(measurement)

    async def _async_post_measurement_event(
            self, measurement: indicam_client.GaugeMeasurement
    ) -> None:
        """Send event with measurement and entity ID, and, store data.

        This method must be run in the event loop.
        """
        indicam_measurement = {
            "body_left": measurement.body_left,
            "body_right": measurement.body_right,
            "body_top": measurement.body_top,
            "body_bottom": measurement.body_bottom,
            "float_top": measurement.float_top,
            "value": measurement.value,
            "entity_id": self.entity_id,
        }
        self.hass.bus.async_fire(INDICAM_MEASUREMENT, indicam_measurement)
        _LOGGER.debug("Indicam Measurement: %d", int(indicam_measurement["value"] * 100))

    async def async_update(self) -> None:
        """Turn the flash on, and initiate an image processing.

        Grabs an image and passes it on to processing. Turns the flash
        on and off around image capture (if a flash was defined). Steps:
            1. If a flash is defined for the camera, turn it on
            2. Grab an image (copied from image_processing component).
            4. Turn the flash off
            5. Pass the image to processing.
        """
        image = await self._grabber.grab_image()
        if not image:
            return
        await self.async_process_image(image.content)
