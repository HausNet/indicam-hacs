""" Client for the Indicam Service API at https://app.hausnet.io/indicam/api.
    See https://docs.hausnet.io/indicam/ for documentation
"""

from __future__ import annotations

import io
from dataclasses import dataclass
import logging

import requests as req

# Camera config items
CAMCONFIG_MAX_KEY = 'full_perc_from_top'
CAMCONFIG_MIN_KEY = 'empty_perc_from_bottom'
# Timeout for service requests
HTTP_TIMEOUT = 30

_LOGGER = logging.getLogger(__name__)


@dataclass
class CamConfig:
    """ Holds the camera configuration. """
    min_perc: float
    max_perc: float


@dataclass
class GaugeMeasurement:
    """ Holds the received measurement for convenient access. """
    body_left: int
    body_right: int
    body_top: int
    body_bottom: int
    float_top: int
    value: float


class IndiCamServiceClient:
    """ API client for the Indicam service. """

    def __init__(self, service_url: str, api_key: str) -> None:
        """ Store the service URL and API key for future use. """
        self._url: str = service_url
        self._req_header = {
            'Accept': 'application/json',
            'Authorization': f'Token {api_key}',
        }

    def get_indicam_id(self, name: str) -> int | None:
        """ Get a camera's service ID, using the given name """
        response = req.get(
            f'{self._url}/indicams/?handle={name}',
            timeout=HTTP_TIMEOUT,
            headers=self._req_header
        )
        if response.status_code != 200:
            _LOGGER.error(
                "Failed to find the indicam - handle=%s, status=%d",
                name,
                response.status_code
            )
            return None
        return response.json()[0]['id']

    def get_camconfig(self, indicam_id: int) -> CamConfig | None:
        """ Get the service's version of the camera configuration. """
        response = req.get(
            f'{self._url}/indicams/{indicam_id}/camconfig_current/',
            timeout=HTTP_TIMEOUT,
            headers=self._req_header
        )
        if response.status_code != 200:
            _LOGGER.error(
                'Unable to fetch camera config - status=%d, indicam_id=%d',
                response.status_code,
                indicam_id
            )
            return None
        svc_cfg = response.json()
        if CAMCONFIG_MAX_KEY not in svc_cfg or CAMCONFIG_MIN_KEY not in svc_cfg:
            _LOGGER.error('Invalid cam_config - json=%s', response.content)
            return None
        return CamConfig(
            min_perc=svc_cfg[CAMCONFIG_MIN_KEY],
            max_perc= svc_cfg[CAMCONFIG_MAX_KEY]
        )

    def create_camconfig(self, indicam_id: int, camconfig: CamConfig) -> bool:
        """ Set the camera configuration at the service. """
        response = req.post(
            f'{self._url}/camconfigs/',
            json={
                'indicam': indicam_id,
                CAMCONFIG_MAX_KEY: camconfig.max_perc,
                CAMCONFIG_MIN_KEY: camconfig.min_perc
            },
            headers=self._req_header,
            timeout=HTTP_TIMEOUT
        )
        if response.status_code != 201:
            _LOGGER.error(
                'Error creating camera config - status=%d, indicam_id=%d',
                response.status_code,
                indicam_id
            )
            return False
        return True


    def upload_image(self, device_name, image) -> int | None:
        """ Post the image to the service, for processing, and return the
            image ID returned by the API, None if an error occurred.
        """
        response = req.post(
            f"{self._url}/images/{device_name}/upload/",
            data=io.BytesIO(bytearray(image)),
            headers=self._req_header | {"Content-type": "image/jpeg"},
            timeout=HTTP_TIMEOUT
        )
        if not response or "error" in response:
            if "error" in response:
                # noinspection PyUnresolvedReferences
                _LOGGER.error(response["error"])
            return None
        return response.json()['image_id']


    def get_measurement(self, image_id: int) -> GaugeMeasurement | None:
        """ Get the measurement for the submitted image. """
        response = req.get(
            f"{self._url}/measurements/?src_image={image_id}",
            headers=self._req_header,
            timeout=HTTP_TIMEOUT
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
            body_left=int(result_json['gauge_left_col']),
            body_right=int(result_json['gauge_right_col']),
            body_top=int(result_json['gauge_top_row']),
            body_bottom=int(result_json['gauge_bottom_row']),
            float_top=int(result_json['float_top_col']),
            value=float(result_json['value'])
        )
        _LOGGER.debug(
            "Measurement received left=%d, right=%d, top=%d, bottom=%d, "
            "float=%d, value=%f",
            measurement.body_left,
            measurement.body_right,
            measurement.body_top,
            measurement.body_bottom,
            measurement.float_top,
            measurement.value
        )
        return measurement
