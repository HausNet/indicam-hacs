"""Indicator Processors"""

import asyncio
import io
import logging
import os
import time
from typing import Optional

import aiofiles
import aiofiles.os
import indicam_client
from PIL import Image, ImageDraw

from homeassistant.const import SERVICE_TURN_ON, SERVICE_TURN_OFF, ATTR_ENTITY_ID, STATE_ON
from homeassistant.components import camera
from homeassistant.components.switch import DOMAIN as DOMAIN_SWITCH
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.pil import draw_box

from .const import FLASH_DELAY_SECONDS, MEASUREMENT_PROCESS_DELAYS, IMAGE_GET_RETRIES, GRAB_TIMEOUT

# The logger
_LOGGER = logging.getLogger(__name__)


class HausNetServiceError(Exception):
    """An exception for client exceptions."""
    pass


class IndicatorProcessor:
    """Base class for indicator processors"""
    pass


class ImageGrabber:
    """Methods to grab images, retry on failure, and control a flash"""

    def __init__(self, hass: HomeAssistant, camera_entity_id, flash_entity_id):
        """Store the configuration"""
        self._camera_entity_id = camera_entity_id
        self._flash_entity_id = flash_entity_id
        self._hass = hass

    async def grab_image(self) -> Optional[bytes]:
        """Grab an image from the configured camera.

           Retries grabbing on failure, and manages turning the flash on and off. If no camera
           entity was defined, just logs an error and returns.
        """
        if self._camera_entity_id is None:
            _LOGGER.error("No camera entity id was set, cannot grab image")
            return None
        await self._turn_flash_on(True)
        get_count = 1
        camera_image: Optional[camera.Image] = None
        while get_count <= IMAGE_GET_RETRIES:
            try:
                camera_image = await camera.async_get_image(self._hass, self._camera_entity_id, timeout=GRAB_TIMEOUT)
            except HomeAssistantError as err:
                _LOGGER.warning(
                    "Error number %d on receive image from entity: %s", get_count, err
                )
            get_count += 1
        await self._turn_flash_on(False)
        if not camera_image:
            _LOGGER.error("No image was received, aborting capture.")
            return None
        return camera_image.content

    async def _turn_flash_on(self, on: bool) -> bool:
        """Control the flash, if present.

        Turn flash on (on=True) or off (on=False). Waits for the flash state
        to change to "on" while sleeping for a period of FLASH_DELAY_SECONDS seconds.

        If, at the end, the flash has not yet been turned on, return False,
        otherwise return True.
        """
        if not self._flash_entity_id:
            return True
        await self._hass.services.async_call(
            DOMAIN_SWITCH,
            SERVICE_TURN_ON if on else SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: self._flash_entity_id},
            blocking=True,
        )
        if not on:
            _LOGGER.debug("Flash turned off")
            return True
        _LOGGER.debug("Waiting for flash to turn on")
        wait_seconds = 0
        while wait_seconds < FLASH_DELAY_SECONDS:
            flash_state = self._hass.states.get(self._flash_entity_id)
            if not flash_state:
                return False
            _LOGGER.debug(
                "Flash state is %s, %d seconds passed", flash_state.state, wait_seconds
            )
            if flash_state.state == STATE_ON:
                break
            await asyncio.sleep(1)
            wait_seconds += 1
        if wait_seconds > FLASH_DELAY_SECONDS:
            _LOGGER.error("Flash did not turn on after %d seconds", wait_seconds)
            return False
        _LOGGER.info("Flash turned on after %d seconds", wait_seconds)
        return True


class IndicatorDecorator:
    """Base class for indicator decorators.

    Decorate an indicator image by overlaying extracted and configuration values over the image.
    """
    pass


class VerticalFloatProcessor(IndicatorProcessor):
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
        self.device_name: str = device_name
        self._api_client: indicam_client.IndiCamServiceClient = client
        self._updated_cam_config: bool = False
        self._indicam_id: int | None = None

    async def process_img(
        self, image: bytes
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
        image_id = await self._api_client.upload_image(self.device_name, image)
        if not image_id:
            _LOGGER.error("Image upload failed, no image ID returned")
            return None, time.monotonic() - start
        for delay in MEASUREMENT_PROCESS_DELAYS:
            if not await self._api_client.measurement_ready(image_id):
                await asyncio.sleep(delay)
                continue
            measurement = await self._api_client.get_measurement(image_id)
            if not measurement:
                break
            return measurement, time.monotonic() - start
        _LOGGER.error(
            "Timed out waiting to retrieve measurement results for: image_id=%d",
            image_id
        )
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
            self._indicam_id = await self._api_client.get_indicam_id(self.device_name)
        return self._indicam_id


class VerticalFloatDecorator(IndicatorDecorator):
    """Saves a snapshot of the original image, and decorates an image based on the measurement."""

    def __init__(self, file_path: str, file_prefix: str):
        """Store configuration values."""
        self._file_path = file_path
        self._file_prefix = file_prefix

    async def decorate_and_save(
            self,
            image: bytes,
            result: indicam_client.GaugeMeasurement,
            cam_config: indicam_client.CamConfig
    ) -> None:
        """Saves the snapshot and decorated result image in the configured location."""
        if not self._file_path:
            return
        snap_file_path = f"{self._file_path}/{self._file_prefix}-snapshot.jpg"
        await self.save_image(image, snap_file_path)
        msr_file_path = f"{self._file_path}/{self._file_prefix}-measure.jpg"
        if not result:
            await self.save_image(None, msr_file_path)
            return
        await self.save_image(self.decorate_image(image, result, cam_config), msr_file_path)

    def decorate_image(
        self, image: bytes, result: indicam_client.GaugeMeasurement, cam_config: indicam_client.CamConfig
    ) -> Image.Image:
        """Decorate the image with extracted data.

        Draw lines on the image showing the scale, camera configuration,
        and measurements.
        """
        img = Image.open(io.BytesIO(bytearray(image))).convert("RGB")
        img_width, img_height = img.size
        draw = ImageDraw.Draw(img)
        self._draw_body(draw, img_height, img_width, result)
        self._draw_scale(draw, result)
        self._draw_min_max(draw, result, cam_config)
        self._draw_float_line(draw, result)
        return img

    @staticmethod
    async def save_image(image: Image.Image | bytes | None, path: str) -> None:
        """Saves an image in the given location. If the image is 'None', deletes the file instead."""
        _LOGGER.info("Saving results image to %s", path)
        await aiofiles.os.makedirs(os.path.dirname(path), exist_ok=True)
        if image is None:
            await aiofiles.os.remove(path)
        if isinstance(image, bytes):
            buffer = io.BytesIO(bytearray(image))
        else:
            buffer = io.BytesIO()
            image.save(buffer, "JPEG")
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
