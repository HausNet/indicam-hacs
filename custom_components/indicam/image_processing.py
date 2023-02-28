"""Support for the HausNet Indicam service."""
from __future__ import annotations

import io
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw
import requests as req
import voluptuous as vol

from homeassistant.components.image_processing import (
    CONF_CONFIDENCE,
    PLATFORM_SCHEMA,
    ImageProcessingEntity,
)
from homeassistant.const import (
    CONF_DEVICE,
    CONF_ENTITY_ID,
    CONF_NAME,
    CONF_SOURCE,
    CONF_TIMEOUT, CONF_URL,
)
from homeassistant.core import HomeAssistant, split_entity_id
from homeassistant.helpers import template
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util.pil import draw_box

_LOGGER = logging.getLogger(__name__)

INDICAM_URL = "https://app.hausnet.io/indicam/api"

ATTR_MATCHES = "matches"
ATTR_SUMMARY = "summary"
ATTR_TOTAL_MATCHES = "total_matches"
ATTR_PROCESS_TIME = "process_time"

CONF_AUTH_KEY = "auth_key"
CONF_DETECTOR = "detector"
CONF_LABELS = "labels"
CONF_AREA = "area"
CONF_TOP = "top"
CONF_BOTTOM = "bottom"
CONF_RIGHT = "right"
CONF_LEFT = "left"
CONF_FILE_OUT = "file_out"

AREA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_BOTTOM, default=1): cv.small_float,
        vol.Optional(CONF_LEFT, default=0): cv.small_float,
        vol.Optional(CONF_RIGHT, default=1): cv.small_float,
        vol.Optional(CONF_TOP, default=0): cv.small_float,
    }
)

LABEL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_AREA): AREA_SCHEMA,
    }
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_URL, default=INDICAM_URL): cv.url,
        vol.Required(CONF_AUTH_KEY): cv.string,
        vol.Required(CONF_DEVICE): cv.string,
        vol.Required(CONF_TIMEOUT, default=90): cv.positive_int,
        vol.Optional(CONF_FILE_OUT, default=[]):
            vol.All(cv.ensure_list, [cv.template]),
        vol.Optional(CONF_CONFIDENCE, default=0.0): vol.Range(min=0, max=100),
        vol.Optional(CONF_LABELS, default=[]): vol.All(
            cv.ensure_list, [vol.Any(cv.string, LABEL_SCHEMA)]
        ),
        vol.Optional(CONF_AREA): AREA_SCHEMA,
    }
)


# noinspection PyUnusedLocal
def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the IndiCam client."""
    service = IndiCamService(
        config[CONF_URL],
        config[CONF_DEVICE],
        config[CONF_AUTH_KEY]
    )

    entities = []
    for camera in config[CONF_SOURCE]:
        entities.append(
            IndiCam(
                hass,
                camera[CONF_ENTITY_ID],
                camera.get(CONF_NAME),
                config,
                service,
            )
        )
    add_entities(entities)


class IndiCam(ImageProcessingEntity):
    """Wrap a camera as an IndiCam."""

    def __init__(
        self,
        hass: HomeAssistant,
        camera_entity: str,
        name: str,
        # detector,
        config: ConfigType,
        service: IndiCamService,
    ) -> None:
        """Initialize a processor entity for a camera."""
        self.hass = hass
        self._camera_entity = camera_entity
        if name:
            self._name = name
        else:
            name = split_entity_id(camera_entity)[1]
            self._name = f"IndiCam {name}"
        self._file_out = config[CONF_FILE_OUT]
        self._service = service
        self._last_result: GaugeMeasurement | None = None
        self._enabled = False

        template.attach(hass, self._file_out)

        self._last_image = None
        self._process_time = 0

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
        if not self._last_result or not self._last_result.value:
            return None
        return self._last_result.value

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {}

    def enable_processing(self, enable=True):
        """Enable the processing of camera images. This is to avoid processing
        images that are acquired from outside the analysis framework.
        """
        self._enabled = enable

    def process_image(self, image):
        """ Process the image, if the processor is enabled. Resets the enabled
            flag after processing, so that it has to be re-enabled for the next
            run.
        """
        _LOGGER.debug("Processing oil tank image")
        # Send the image for processing
        self._last_result, cam_config, elapsed_time = \
            self._service.process_img(image)
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
            self._save_image(image, self._last_result, cam_config, paths)
        self._process_time = elapsed_time

    def _save_image(
            self,
            image,
            measurement: GaugeMeasurement,
            cam_config: CamConfig,
            paths: [str]
    ) -> None:
        img = Image.open(io.BytesIO(bytearray(image))).convert("RGB")
        img_width, img_height = img.size
        draw = ImageDraw.Draw(img)
        self._draw_body(draw, img_height, img_width, measurement)
        self._draw_scale(draw, measurement)
        self._draw_min_max(draw, measurement, cam_config)
        self._draw_float_line(draw, measurement)
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


@dataclass
class GaugeMeasurement:
    """ Holds the received measurement for convenient access. """
    body_left: int
    body_right: int
    body_top: int
    body_bottom: int
    float_top: int
    value: float


@dataclass
class CamConfig:
    """ Holds the camera configuration. """
    min_perc: float
    max_perc: float


class IndiCamService:
    """ A class that wraps the IndiCam service parameters, and provide a
        unified post method for the image.
    """

    def __init__(self, url: str, device: str, auth_key: str) -> None:
        """ Set up the service URL and call headers. """
        self._url = url
        self._device = device
        self._auth_header = {"Authorization": f"Token {auth_key}"}

    def process_img(self, image) -> \
            (GaugeMeasurement | None, CamConfig | None, float | None):
        """ Process the image using the service, then fetch the measurement
            results. Returns the measurement results, the camera configuration,
            and the elapsed time.
        """
        start = time.monotonic()
        image_id = self._submit_image(image)
        if not image_id:
            return None, None, time.monotonic() - start
        measurement = self._get_measurement(image_id)
        if not measurement:
            return None, None, time.monotonic() - start
        cam_config = self._get_cam_config(image_id)
        if not cam_config:
            return measurement, None, time.monotonic() - start
        return measurement, cam_config, time.monotonic() - start

    def _submit_image(self, image) -> int | None:
        """ Post the image to the service, for processing, and return the
            image ID returned by the API, None if an error occurred.
        """
        response = req.post(
            f"{self._url}/images/{self._device}/upload/",
            data=io.BytesIO(bytearray(image)),
            headers=self._auth_header | {"Content-type": "image/jpeg"},
            timeout=30
        )
        if not response or "error" in response:
            if "error" in response:
                # noinspection PyUnresolvedReferences
                _LOGGER.error(response["error"])
            return None
        return response.json()['image_id']

    def _get_measurement(self, image_id: int) -> GaugeMeasurement | None:
        """ Get the measurement for the submitted image. """
        response = req.get(
            f"{self._url}/measurements/?src_image={image_id}",
            headers=self._auth_header,
            timeout=30
        )
        if not response or response.status_code != 200:
            _LOGGER.error(
                "Error retrieving measurement for image %d', status=%d",
                image_id,
                None if not response else response.status_code
            )
            return None
        result_json = response.json()[0]
        measurement = GaugeMeasurement(
            body_left=result_json['gauge_left_col'],
            body_right=result_json['gauge_right_col'],
            body_top=result_json['gauge_top_row'],
            body_bottom=result_json['gauge_bottom_row'],
            float_top=result_json['float_top_col'],
            value=result_json['value']
        )
        return measurement

    def _get_cam_config(self, image_id: int) -> CamConfig | None:
        """ Get the camera configuration for the specified image. """
        response = req.get(
            f"{self._url}/camconfigs/?images={image_id}",
            headers=self._auth_header,
            timeout=30
        )
        if not response or response.status_code != 200:
            _LOGGER.error(
                "Error retrieving cam config for image id=%d, status=%d",
                image_id,
                None if not response else response.status_code
            )
            return None
        config_json = response.json()[0]
        cam_config = CamConfig(
            min_perc=config_json['empty_perc_from_bottom'],
            max_perc=config_json['full_perc_from_top']
        )
        return cam_config
