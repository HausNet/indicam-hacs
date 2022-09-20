"""Support for the HausNet Indicam service."""
from __future__ import annotations

import io
import logging
import os
import time
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
    timeout_seconds: int = config[CONF_TIMEOUT]

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
        self._last_result: dict[str, Any] | None = None
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
        if self._last_result is None or \
                self._last_result["measurement"] is None:
            return None
        return self._last_result["measurement"]["value"]

    @property
    def extra_state_attributes(self):
        """Return device specific state attributes."""
        return {}

    def enable_processing(self, enable=True):
        """Enable the processing of camera images. This is to avoid processing
        images that are acquired outside of the analysis framework.
        """
        self._enabled = enable

    def _save_image(self, image, measurement, cam_config, paths):
        img = Image.open(io.BytesIO(bytearray(image))).convert("RGB")
        img_width, img_height = img.size
        draw = ImageDraw.Draw(img)
        self._draw_box(draw, img_height, img_width, measurement)
        self._draw_float_line(draw, measurement)
        for path in paths:
            _LOGGER.info("Saving results image to %s", path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            img.save(path)
        raw_img = Image.open(io.BytesIO(bytearray(image))).convert("RGB")
        _LOGGER.info("Saving raw image to /tmp/indicam-oil-tank-raw.jpg")
        raw_img.save("/tmp/indicam-oil-tank-raw.jpg")

    @staticmethod
    def _draw_box(
            draw: ImageDraw.Draw,
            height: int,
            width: int,
            measurement: dict[str, Any]
    ) -> None:
        """ Draw the (estimated) borders of the gauge. Drawn in yellow.  """
        left = measurement["gauge_left_col"] / width
        right = measurement["gauge_right_col"] / width
        top = measurement["gauge_top_row"] / height
        bottom = measurement["gauge_bottom_row"] / height
        box = (top, left, bottom, right)
        draw_box(draw, box, width, height, "", (255, 255, 0))

    @staticmethod
    def _draw_float_line(
            draw: ImageDraw.Draw,
            measurement: dict[str, Any]
    ) -> None:
        """ Calculate where the float top measurement line go, and draw it on the image being constructed.
            Drawn in red.
        """
        left = measurement['gauge_left_col']
        right = measurement['gauge_right_col']
        row = measurement['float_top_col']
        draw.line(
            [(left, row), (right, row)],
            width=10,
            fill=(255, 0, 0)
        )

    def process_image(self, image):
        """ Process the image, if the processor is enabled. Resets the enabled
            flag after processing, so that it has to be re-enabled for the next
            run.
        """
        _LOGGER.debug("Processing oil tank image")

        # Send the image for processing
        self._last_result, elapsed_time = self._service.process_img(image)
        if not self._last_result:
            self._process_time = elapsed_time
            return

        # Save Images
        if self._last_result["measurement"] and self._file_out:
            paths = []
            for path_template in self._file_out:
                if isinstance(path_template, template.Template):
                    paths.append(
                        path_template.render(camera_entity=self._camera_entity)
                    )
                else:
                    paths.append(path_template)
            self._save_image(
                image,
                self._last_result["measurement"],
                self._last_result["cam_config"],
                paths,
            )

        self._process_time = elapsed_time


class IndiCamService:
    """ A class that wraps the IndiCam service parameters, and provide a
        unified post method for the image.
    """

    def __init__(self, url: str, device: str, auth_key: str) -> None:
        """Set up the service URL and call headers."""
        self._url = url
        self._device = device
        self._auth_header = {"Authorization": f"Token {auth_key}"}

    def process_img(self, image) -> (dict[str, Any] | None, float):
        """Process the image using the service, then fetch the measurement
        results.
        """
        start = time.monotonic()
        response = req.post(
            f"{self._url}/images/{self._device}/upload/",
            data=io.BytesIO(bytearray(image)),
            headers=self._auth_header | {"Content-type": "image/jpeg"},
        )
        _LOGGER.debug(
            "Sent image to service: status=%s duration=%s",
            response.status_code,
            time.monotonic() - start,
        )
        if not response or "error" in response:
            if "error" in response:
                _LOGGER.error(response["error"])
            return None, time.monotonic() - start
        result: dict[str, Any] = response.json()
        response = req.get(
            f"{self._url}/measurements/?src_image={result['image_id']}",
            headers=self._auth_header,
        )
        if not response or response.status_code != 200:
            _LOGGER.error(
                f"Error retrieving measurement for image {result['image_id']}: "
                f"status={response.status_code}"
            )
            result["measurement"] = None
        else:
            result["measurement"] = response.json()[0]
        response = req.get(
            f"{self._url}/camconfigs/?images={result['image_id']}",
            headers=self._auth_header,
        )
        if not response or response.status_code != 200:
            _LOGGER.error(
                f"Error retrieving cam config for image {result['image_id']}: "
                f"status={response.status_code}"
            )
            result["cam_config"] = None
        else:
            result["cam_config"] = response.json()[0]
        return result, time.monotonic() - start
