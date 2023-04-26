"""Support for the HausNet Indicam service."""

from __future__ import annotations

import io
import logging
import os
import time

from PIL import Image, ImageDraw
import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.components.image_processing import (
    PLATFORM_SCHEMA as IMGPROC_PLATFORM_SCHEMA,
    ImageProcessingEntity
)
from homeassistant.const import (
    CONF_DEVICE,
    CONF_NAME,
    CONF_SOURCE,
    CONF_URL,
    CONF_MINIMUM,
    CONF_MAXIMUM,
    EVENT_HOMEASSISTANT_STARTED
)
from homeassistant.core import HomeAssistant, split_entity_id, Event
from homeassistant.helpers import template
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util.pil import draw_box

from .api_client import CamConfig, IndiCamServiceClient, GaugeMeasurement

_LOGGER = logging.getLogger(__name__)
INDICAM_URL = "https://app.hausnet.io/indicam/api"

ATTR_MATCHES = "matches"
ATTR_SUMMARY = "summary"
ATTR_TOTAL_MATCHES = "total_matches"
ATTR_PROCESS_TIME = "process_time"

CONF_AUTH_KEY = "auth_key"
CONF_FILE_OUT = "file_out"

SOURCE_SCHEMA = cv.vol.Schema(
    {
        vol.Required(
            CONF_NAME,
            description="Device name at Indicam Service"
        ): cv.string,
        vol.Required(
            CONF_SOURCE,
            description="Source camera entity ID"
        ): cv.string,
        vol.Optional(
            CONF_MINIMUM,
            default=0,
            description="Distance of empty mark from bottom of gauge body, " +
                        "as a decimal fraction of gauge body height"
        ): cv.positive_float,
        vol.Optional(
            CONF_MAXIMUM,
            default=0,
            description="Distance of full mark from top of gauge body, " +
                        "as a decimal fraction of gauge body height"
        ): cv.positive_float,
        vol.Optional(
            CONF_FILE_OUT,
            default=[],
            description="Path to save decorated image to"
        ): cv.string
    }
)

PLATFORM_SCHEMA = IMGPROC_PLATFORM_SCHEMA.extend(
    {
        vol.Optional(
            CONF_URL,
            default=INDICAM_URL,
            description="The HausNet IndiCam API URL"
        ): cv.url,
        vol.Required(CONF_AUTH_KEY, description="API Authentication Token"):
            cv.string,
        vol.Optional(CONF_DEVICE): vol.All(cv.ensure_list, [SOURCE_SCHEMA]),
    }
)

# noinspection PyUnusedLocal
async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the IndiCam client."""
    client = IndiCamServiceClient(config[CONF_URL], config[CONF_AUTH_KEY])

    async def setup_service(_event: Event) -> None:
        """ Synchronize camera configuration. """
        for indicam in entities:
            await indicam.post_hass_init_setup()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, setup_service)
    entities = []
    for camera in config[CONF_DEVICE]:
        cam_config = CamConfig(
            min_perc=camera[CONF_MINIMUM], max_perc=camera[CONF_MAXIMUM]
        )
        processor = IndiCamProcessor(client, camera[CONF_NAME], cam_config)
        entities.append(
            IndiCam(
                hass,
                camera[CONF_SOURCE],
                camera[CONF_NAME],
                cam_config,
                processor,
                camera[CONF_FILE_OUT]
            )
        )
    add_entities(entities)


class HausNetServiceError(Exception):
    """ A custom exception for situations where the HausNet service is not
        accessible.
    """


class IndiCam(ImageProcessingEntity):
    """Wrap a camera as an IndiCam."""

    def __init__(
        self,
        hass: HomeAssistant,
        camera_entity: str,
        name: str,
        cam_config: CamConfig,
        processor: IndiCamProcessor,
        file_out: str
    ) -> None:
        """Initialize a processor entity for a camera."""
        self.hass = hass
        self._camera_entity = camera_entity
        if name:
            self._name = name
        else:
            name = split_entity_id(camera_entity)[1]
            self._name = f"IndiCam {name}"
        self._file_out = file_out
        self._processor = processor
        self._last_result: GaugeMeasurement | None = None
        self._enabled = False
        self._cam_config = cam_config

        template.attach(hass, self._file_out)

        self._last_image = None
        self._process_time = 0

    async def post_hass_init_setup(self):
        """ To be called after Home Assistant has finished initializing """
        await self.hass.async_add_executor_job(self.setup_processor)

    def setup_processor(self):
        """ Set up the processor after HASS has been initialized. If an error
            occurs, notify the user.
        """
        try:
            self._processor.setup()
        except Exception as err:
            persistent_notification.create(
                self.hass,
                "HausNet Indicam service connection error, please check your "
                "<a href=\"/config/logs\">logs</a> for "
                "details.",
                title="HausNet service error",
                notification_id=self._name + "_setup"
            )
            raise err

    @property
    def camera_entity(self):
        """Return camera entity id from process pictures."""
        return self._camera_entity

    @property
    def name(self):
        """Return the name of the image processor."""
        return self._name

    @property
    def state(self):
        """Return the state of the entity -- the last measurement made."""
        if not self._last_result or self._last_result.value is None:
            return None
        return self._last_result.value

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {}

    def process_image(self, image):
        """ Process the given image. """
        _LOGGER.debug("Processing oil tank image")
        # Send the image for processing
        self._last_result, elapsed_time = \
            self._processor.process_img(image)
        if not self._last_result or not self._last_result.value:
            self._process_time = elapsed_time
            return
        # Save Images
        if self._last_result.value and self._file_out:
            paths = []
            for path_template in self._file_out:
                if isinstance(path_template, template.Template):
                    paths.append(
                        path_template.render(camera_entity=self._camera_entity)
                    )
                else:
                    paths.append(path_template)
            self._save_image(image, paths)
        self._process_time = elapsed_time

    def _save_image(
            self,
            image,
            paths: [str]
    ) -> None:
        img = Image.open(io.BytesIO(bytearray(image))).convert("RGB")
        img_width, img_height = img.size
        draw = ImageDraw.Draw(img)
        self._draw_body(draw, img_height, img_width, self._last_result)
        self._draw_scale(draw, self._last_result)
        self._draw_min_max(draw, self._last_result, self._cam_config)
        self._draw_float_line(draw, self._last_result)
        for path in paths:
            _LOGGER.info("Saving results image to %s", path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            img.save(path)
        raw_img = Image.open(io.BytesIO(bytearray(image))).convert("RGB")
        _LOGGER.info("Saving raw image to /tmp/indicam-oil-tank-raw.jpg")
        raw_img.save("/tmp/indicam-oil-tank-raw.jpg")

    @staticmethod
    def _draw_body(
            draw: ImageDraw.Draw,
            img_height: int,
            img_width: int,
            msr: GaugeMeasurement
    ) -> None:
        """ Draw the (estimated) borders of the gauge body.
            Drawn in yellow.
        """
        top = msr.body_top / img_height
        left = msr.body_left / img_width
        bottom = msr.body_bottom / img_height
        right = msr.body_right / img_width
        box = (top, left, bottom, right)
        draw_box(draw, box, img_width, img_height, color=(255, 255, 0))

    @staticmethod
    def _draw_scale(draw: ImageDraw.Draw, msr: GaugeMeasurement) -> None:
        """ Draw a scale with a mark at every 10% of the gauge height
            down the left hand side of the gauge.
        """
        height = msr.body_bottom - msr.body_top + 1
        width = msr.body_right - msr.body_left + 1
        mark_width = 0.1 * width
        for perc_mark in range(0, 101, 10):
            mark_loc = round(msr.body_top + height * perc_mark / 100)
            draw.line(
                [
                    (msr.body_left - mark_width, mark_loc),
                    (msr.body_left, mark_loc)
                ],
                width=10,
                fill=(255, 255, 0)
            )

    @staticmethod
    def _draw_min_max(
            draw: ImageDraw.Draw, msr: GaugeMeasurement, cam_conf: CamConfig
    ) -> None:
        """ Draw the min and max measurement lines in the same color as the
            float measurement line.
        """
        height = (msr.body_bottom - msr.body_top)
        max_row = msr.body_top + cam_conf.max_perc * height
        min_row = msr.body_bottom - cam_conf.min_perc * height
        draw.line(
            [(msr.body_left, max_row), (msr.body_right, max_row)],
            width=10,
            fill=(255, 0, 0)
        )
        draw.line(
            [(msr.body_left, min_row), (msr.body_right, min_row)],
            width=10,
            fill=(255, 0, 0)
        )

    @staticmethod
    def _draw_float_line(draw: ImageDraw.Draw, msr: GaugeMeasurement) -> None:
        """ Draw the float measurement line on the image being constructed.
            Drawn in red.
        """
        draw.line(
            [(msr.body_left, msr.float_top), (msr.body_right, msr.float_top)],
            width=10,
            fill=(255, 0, 0)
        )


class IndiCamProcessor:
    """ A class that wraps the IndiCam service for one camera, and provide a
        unified post method for the image. Note that the camera configuration
        is wholly controlled from Home Assistant - when a new value is set
        in the config, it is changed at the service as part of the startup.
    """

    def __init__(
            self,
            client: IndiCamServiceClient,
            device_name: str,
            camconfig: CamConfig
    ) -> None:
        """ Set up the service URL and call headers. """
        self._device_name = device_name
        self._api_client = client
        self._indicam_id: int | None = None
        self._camconfig = camconfig
        self._updated_cam_config = False

    def setup(self):
        """ Should be called after Home Assistant completes setup. """
        self._indicam_id = self._api_client.get_indicam_id(self._device_name)
        self.update_cam_config()

    def update_cam_config(self):
        """ Checks to see if the update has been done yet. If so, returns
            without changing anything. Otherwise, compares the local config to
            the config at the service, and if they're not the same, sends the
            local config to the service. Sets the update done flag to indicate
            the procedure does not have to be executed again.

            Returns "True" if update is completed
        """
        if self._updated_cam_config:
            return
        _LOGGER.debug(
            "Fetching service camconfig for indicam ID=%d", self._indicam_id
        )
        svc_cfg = self._api_client.get_camconfig(self._indicam_id)
        if not svc_cfg:
            raise HausNetServiceError(
                "Could not retrieve camera configuration from the"
                "HausNet service"
            )
        if svc_cfg == self._camconfig:
            _LOGGER.debug('Local cam config the same as at service')
            self._updated_cam_config = True
            return
        cfg_created = self._api_client.create_camconfig(
            self._indicam_id, self._camconfig
        )
        if not cfg_created:
            raise HausNetServiceError(
                "Could not create a new camera configuration at the"
                "HausNet service"
            )
        self._updated_cam_config = True
        return

    def process_img(self, image) -> (GaugeMeasurement | None, float | None):
        """ First, updates the service camera configuration if it has not
            yet been done. Then, process the image using the service, and
             fetch the measurement results. Returns the measurement results and
             the elapsed time.
        """
        start = time.monotonic()
        if not self._updated_cam_config:
            self.update_cam_config()
        image_id = self._api_client.upload_image(self._device_name, image)
        if not image_id:
            return None, None, time.monotonic() - start
        measurement = self._api_client.get_measurement(image_id)
        if not measurement:
            return None, None, time.monotonic() - start
        return measurement, time.monotonic() - start
