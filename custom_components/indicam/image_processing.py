"""IndiCam component's image processing code.

Provides conversion of snapshots of analog sensor dials and gauges to
sensor values via the IndiCam image processing service.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
import datetime as dt
import io
import logging
import os
import time
from typing import Any

import aiofiles
import aiofiles.os
import indicam_client
from PIL import Image, ImageDraw
import voluptuous as vol

from homeassistant.components.camera import DOMAIN as DOMAIN_CAMERA
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
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, PlatformNotReady
from homeassistant.helpers import template
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util.pil import draw_box

from .const import (
    ATTR_GAUGE_MEASUREMENT,
    CONF_AUTH_KEY,
    CONF_CAMERA_ENTITY_ID,
    CONF_CYCLE_SECONDS,
    CONF_FLASH_ENTITY_ID,
    CONF_INDICAMS,
    CONF_PATH_OUT,
    FLASH_DELAY,
    INDICAM_MEASUREMENT,
    INDICAM_URL,
    OIL_CAM_CYCLE_SECONDS_DEFAULT,
    OIL_CAM_CYCLE_SECONDS_MINIMUM,
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
            CONF_CYCLE_SECONDS, default=OIL_CAM_CYCLE_SECONDS_DEFAULT
        ): vol.All(vol.Coerce(int), vol.Range(min=OIL_CAM_CYCLE_SECONDS_MINIMUM)),
    }
)
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_AUTH_KEY): str,
        vol.Required(CONF_INDICAMS): vol.All(cv.ensure_list, [INDICAM_SCHEMA]),
    }
)
# How many times, and for how long to wait for a measurement to be made
MEASUREMENT_PROCESS_DELAYS = [1, 5, 25, 60, 90]


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
    """Set up the IndiCam platform."""
    session = async_get_clientsession(hass)
    client = indicam_client.IndiCamServiceClient(
        session, INDICAM_URL, config[CONF_AUTH_KEY]
    )
    connect_status = await client.test_connect()
    if connect_status == indicam_client.CONNECT_FAIL:
        raise PlatformNotReady(
            f"Connection failed trying to connect to client at {INDICAM_URL}"
        )
    if connect_status == indicam_client.CONNECT_AUTH_FAIL:
        raise ConfigEntryAuthFailed("Authentication failed")
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
        processor = IndiCamProcessor(hass, client, indicam_name, cam_config)
        entities.append(
            IndiCamImageProcessingEntity(
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
        for indicam in entities:
            await indicam.start_cycle()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, start_cycles)


class HausNetServiceError(Exception):
    """An exception for client exceptions."""


# Simplify definition of calling a function after a delay
CallLater = Callable[[dt.datetime], Coroutine[Any, Any, None] | None]


class IndiCamImageProcessingEntity(ImageProcessingEntity):
    """Wrap a camera as an IndiCam."""

    def __init__(
        self,
        name: str,
        camera_entity_id: str,
        cycle_time: dt.timedelta,
        flash_entity_id: str,
        outfile_path: str,
        processor: IndiCamProcessor,
    ) -> None:
        """Initialize a processor entity for a camera."""
        self._camera_entity_id = camera_entity_id
        self._name = name
        self._camera_entity_id = camera_entity_id
        self._cycle_time = cycle_time
        self._flash_entity_id = flash_entity_id
        self._outfile_path = outfile_path
        self._processor = processor
        self._last_result: indicam_client.GaugeMeasurement | None = None
        self._enabled = False
        template.attach(self.hass, self._outfile_path)

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

    async def async_process_image(self, image: bytes) -> None:
        """Process the given image."""
        _LOGGER.info("Processing oil tank image")
        # Send the image for processing
        measurement, elapsed_time = await self._processor.process_img(image)
        if not measurement or not measurement.value:
            self._last_result = None
            return
        self._last_result = measurement
        # Save Images
        if self._outfile_path:
            msr_file_path = f"{self._outfile_path}/{self._name}-measure.jpg"
            await self._save_image(self._decorate_image(image), msr_file_path)
            snap_file_path = f"{self._outfile_path}/{self._name}-snapshot.jpg"
            await self._save_image(image, snap_file_path)
        self._last_result = measurement
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

    async def async_update(self) -> None:
        """Turn the flash on, and initiate an image processing.

        Grabs an image and passes it on to processing. Turns the flash
        on and off around image capture (if a flash was defined). Steps:
            1. If a flash is defined for the camera, turn it on
            2. Wait FLASH_DELAY seconds for flash to turn on and reach full
               brightness.
        Then, via grab_and_process_image:
            3. Grab an image via the parent async_update.
            4. Turn the flash off
            5. Pass the image to processing.
        """
        await self._turn_flash_on(True)
        await super().async_update()
        await self._turn_flash_on(False)

    async def _turn_flash_on(self, on: bool) -> None:
        """Control the flash, if present.

        Turn flash on (on=True) or off (on=False). Sleeps for a short period
        to allow the flash to reach full brightness.
        """
        if not self._flash_entity_id:
            return
        await self.hass.services.async_call(
            DOMAIN_SWITCH,
            SERVICE_TURN_ON if on else SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: self._flash_entity_id},
            blocking=True,
        )
        if on:
            await asyncio.sleep(FLASH_DELAY)

    def _decorate_image(self, image):
        """Decorate the image with extracted data.

        Draw lines on the image showing the scale, camera configuration,
        and measurements.
        """
        img = Image.open(io.BytesIO(bytearray(image))).convert("RGB")
        img_width, img_height = img.size
        draw = ImageDraw.Draw(img)
        self._draw_body(draw, img_height, img_width, self._last_result)
        self._draw_scale(draw, self._last_result)
        self._draw_min_max(draw, self._last_result, self._processor.cam_config)
        self._draw_float_line(draw, self._last_result)
        return img

    @staticmethod
    async def _save_image(image: Image.Image | bytes, path: str) -> None:
        _LOGGER.info("Saving results image to %s", path)
        if isinstance(image, bytes):
            buffer = io.BytesIO(bytearray(image))
        else:
            buffer = io.BytesIO()
            image.save(buffer, "JPEG")
        await aiofiles.os.makedirs(os.path.dirname(path), exist_ok=True)
        async with aiofiles.open(path, "wb") as file:
            await file.write(buffer.getbuffer())

    @staticmethod
    def _draw_body(
        draw: ImageDraw.ImageDraw,
        img_height: int,
        img_width: int,
        msr: indicam_client.GaugeMeasurement,
    ) -> None:
        """Draw the (estimated) borders of the gauge body. Drawn in yellow."""
        top = msr.body_top / img_height
        left = msr.body_left / img_width
        bottom = msr.body_bottom / img_height
        right = msr.body_right / img_width
        box = (top, left, bottom, right)
        draw_box(draw, box, img_width, img_height, color=(255, 255, 0))

    @staticmethod
    def _draw_scale(
        draw: ImageDraw.ImageDraw, msr: indicam_client.GaugeMeasurement
    ) -> None:
        """Draw a scale on the image to guide the setting of offsets.

        Draw a scale with a mark at every 10% of the gauge height
        down the left hand side of the gauge.
        """
        height = msr.body_bottom - msr.body_top + 1
        width = msr.body_right - msr.body_left + 1
        mark_width = 0.1 * width
        for perc_mark in range(0, 101, 10):
            mark_loc = round(msr.body_top + height * perc_mark / 100)
            line = [(msr.body_left - mark_width, mark_loc), (msr.body_left, mark_loc)]
            draw.line(line, width=10, fill=(255, 255, 0))

    @staticmethod
    def _draw_min_max(
        draw: ImageDraw.ImageDraw,
        msr: indicam_client.GaugeMeasurement,
        cam_conf: indicam_client.CamConfig,
    ) -> None:
        """Draw the min and max measurement lines.

        Lines are drawn in the same color as the float measurement line, at
        the specified offset percentages from the top and the bottom..
        """
        height = msr.body_bottom - msr.body_top
        max_row = msr.body_top + cam_conf.max_perc * height
        min_row = msr.body_bottom - cam_conf.min_perc * height
        draw.line(
            [(msr.body_left, max_row), (msr.body_right, max_row)],
            width=10,
            fill=(255, 0, 0),
        )
        draw.line(
            [(msr.body_left, min_row), (msr.body_right, min_row)],
            width=10,
            fill=(255, 0, 0),
        )

    @staticmethod
    def _draw_float_line(
        draw: ImageDraw.ImageDraw, msr: indicam_client.GaugeMeasurement
    ) -> None:
        """Mark the float position.

        Draw the float measurement line on the image being constructed.
        Drawn in red.
        """
        line = [(msr.body_left, msr.float_top), (msr.body_right, msr.float_top)]
        draw.line(line, width=10, fill=(255, 0, 0))


class IndiCamProcessor:
    """Wraps processing the image via the IndiCam client.

    A class that wraps the IndiCam service for one camera, and provide a
    unified post method for the image. Note that the camera configuration
    is wholly controlled from Home Assistant - when a new value is set
    in the config, it is changed at the service as part of the startup.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: indicam_client.IndiCamServiceClient,
        device_name: str,
        camconfig: indicam_client.CamConfig,
    ) -> None:
        """Set up the service URL and call headers."""
        self.cam_config = camconfig
        self._hass = hass
        self._device_name: str = device_name
        self._api_client: indicam_client.IndiCamServiceClient = client
        self._updated_cam_config: bool = False
        self._indicam_id: int | None = None

    async def process_img(
        self, image
    ) -> tuple[indicam_client.GaugeMeasurement | None, float | None]:
        """Send the image to the service, and fetch the result afterward.

        First, updates the service camera configuration if it has not
        yet been done. Then, process the image using the service, and
        fetch the measurement results. Returns the measurement results and
        the elapsed time.
        """
        start = time.monotonic()
        if not self._updated_cam_config:
            await self._update_cam_config()
        image_id = await self._api_client.upload_image(self._device_name, image)
        if not image_id:
            return None, time.monotonic() - start
        for delay in MEASUREMENT_PROCESS_DELAYS:
            if not await self._api_client.measurement_ready(image_id):
                await asyncio.sleep(delay)
                continue
            measurement = await self._api_client.get_measurement(image_id)
            if not measurement:
                break
            return measurement, time.monotonic() - start
        return None, time.monotonic() - start

    async def _update_cam_config(self):
        """Update the camera config at the service if needed.

        Checks to see if the update has been done yet. If so, returns
        without changing anything. Otherwise, compares the local config to
        the config at the service, and if they're not the same, sends the
        local config to the service. Sets the update done flag to indicate
        the procedure does not have to be executed again.

        Returns "True" if update is completed
        """
        if self._updated_cam_config:
            return
        _LOGGER.debug(
            "Fetching service camconfig for indicam ID=%d", await self._get_indicam_id()
        )
        svc_cfg = await self._api_client.get_camconfig(await self._get_indicam_id())
        if not svc_cfg:
            raise HausNetServiceError(
                "Could not retrieve camera configuration from the service"
            )
        if svc_cfg == self.cam_config:
            _LOGGER.debug("Local cam config the same as at service")
            self._updated_cam_config = True
            return
        cfg_created = await self._api_client.create_camconfig(
            await self._get_indicam_id(), self.cam_config
        )
        if not cfg_created:
            raise HausNetServiceError(
                "Could not create a new camera configuration at the service"
            )
        self._updated_cam_config = True
        return

    async def _get_indicam_id(self) -> int | None:
        """If the IndiCam ID has not yet been retrieved, get it."""
        if not self._indicam_id:
            self._indicam_id = await self._api_client.get_indicam_id(self._device_name)
        return self._indicam_id
