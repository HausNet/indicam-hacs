"""Image processing tests.

Modeled on the parent image_processing component.
"""

from collections.abc import Generator
from contextlib import contextmanager
import os.path
from unittest import mock

from indicam_client import CONNECT_OK, CamConfig, GaugeMeasurement, IndiCamServiceClient
from PIL import Image
import pytest

from homeassistant.components.demo import DOMAIN as DEMO_DOMAIN
from homeassistant.components.image_processing import DOMAIN as IP_DOMAIN, SERVICE_SCAN
from homeassistant.custom_components.indicam.const import (
    ATTR_GAUGE_MEASUREMENT,
    CONF_AUTH_KEY,
    CONF_CAMERA_ENTITY_ID,
    CONF_INDICAMS,
    DOMAIN as INDICAM_DOMAIN,
    INDICAM_MEASUREMENT,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_MAXIMUM,
    CONF_MINIMUM,
    CONF_NAME,
    CONF_PLATFORM,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from tests.common import async_capture_events

CAM_CONFIG = CamConfig(min_perc=0.1, max_perc=0.1)
CONFIG = {
    IP_DOMAIN: [
        {
            CONF_PLATFORM: INDICAM_DOMAIN,
            CONF_AUTH_KEY: "test_key",
            CONF_INDICAMS: [
                {
                    CONF_NAME: "indicam test",
                    CONF_CAMERA_ENTITY_ID: "camera.demo_camera",
                    CONF_MAXIMUM: CAM_CONFIG.max_perc,
                    CONF_MINIMUM: CAM_CONFIG.min_perc,
                }
            ],
        }
    ],
    DEMO_DOMAIN: {},
}
MEASUREMENT = GaugeMeasurement(
    body_left=100,
    body_right=200,
    body_top=100,
    body_bottom=500,
    float_top=300,
    value=0.5,
)
INDICAM_ENTITY_ID = "image_processing.indicam_test"
IMAGE_ID = 1001
INDICAM_ID = 1


@pytest.fixture(autouse=True)
async def setup_homeassistant(hass: HomeAssistant):
    """Set up the homeassistant integration."""
    await async_setup_component(hass, "homeassistant", {})


def image_for_test() -> Image.Image:
    """Load the test image from the test directory."""
    filepath = os.path.dirname(os.path.realpath(__file__)) + "/test_img.jpg"
    return Image.open(filepath).convert("RGB")


async def setup_indicam(hass):
    """Set up indicam-specific stuff.

    Has to be called after the IndiCamServiceClient has been mocked.
    """
    await async_setup_component(hass, DEMO_DOMAIN, CONFIG)
    await async_setup_component(hass, IP_DOMAIN, CONFIG)
    await hass.async_block_till_done()
    return async_capture_events(hass, "image_processing.detect_face")


@contextmanager
def indicam_client_mock(
    measurement: GaugeMeasurement | None = MEASUREMENT,
) -> Generator[mock.MagicMock, None, None]:
    """Create a mock client instance to test the client's async methods."""
    mock_instance = mock.create_autospec(IndiCamServiceClient)
    mock_instance.test_connect.return_value = CONNECT_OK
    mock_instance.get_indicam_id.return_value = INDICAM_ID
    mock_instance.get_camconfig.return_value = CAM_CONFIG
    mock_instance.upload_image.return_value = IMAGE_ID
    mock_instance.get_measurement.return_value = measurement

    with mock.patch("indicam_client.IndiCamServiceClient") as class_mock:
        class_mock.return_value = mock_instance
        yield mock_instance


async def image_processing_scan(hass) -> None:
    """Issue a scan request to the image processing component."""
    await hass.async_add_job(
        hass.services.async_call(
            IP_DOMAIN, SERVICE_SCAN, {ATTR_ENTITY_ID: INDICAM_ENTITY_ID}
        )
    )


@mock.patch(
    "homeassistant.components.demo.camera.Path.read_bytes",
    return_value=image_for_test(),
)
async def test_image_uploaded(mock_camera_read, hass: HomeAssistant) -> None:
    """Verify that a captured image is uploaded to the service for processing."""
    with indicam_client_mock(None) as mock_client:
        await setup_indicam(hass)
        await image_processing_scan(hass)
        await hass.async_block_till_done()
    mock_client.upload_image.assert_called_once_with(
        "indicam test", mock_camera_read.return_value
    )


# Have the camera return something that looks like an image.
@mock.patch(
    "homeassistant.components.demo.camera.Path.read_bytes",
    return_value=b"Test",
)
async def test_get_image_from_camera(mock_camera_read, hass: HomeAssistant) -> None:
    """Grab an image from camera entity."""
    with indicam_client_mock():
        await setup_indicam(hass)
        await image_processing_scan(hass)
        await hass.async_block_till_done()
    assert mock_camera_read.called


# Return the test image from the camera
@mock.patch(
    "homeassistant.components.demo.camera.Path.read_bytes",
    return_value=image_for_test(),
)
async def test_measurement_event(mock_camera_read, hass: HomeAssistant) -> None:
    """Verify that an event broadcasting the (mock) measurement is generated."""
    with indicam_client_mock() as mock_client:
        await setup_indicam(hass)
        events = async_capture_events(hass, INDICAM_MEASUREMENT)
        await image_processing_scan(hass)
        await hass.async_block_till_done()
    mock_client.upload_image.assert_called_once_with(
        "indicam test", mock_camera_read.return_value
    )
    mock_client.get_measurement.assert_called_once_with(IMAGE_ID)
    event = events[0]
    assert event.event_type == INDICAM_MEASUREMENT
    data = event.data
    assert data.get("entity_id") == INDICAM_ENTITY_ID
    assert MEASUREMENT.body_bottom == data.get("body_bottom")
    assert MEASUREMENT.body_top == data.get("body_top")
    assert MEASUREMENT.body_left == data.get("body_left")
    assert MEASUREMENT.body_right == data.get("body_right")
    assert MEASUREMENT.float_top == data.get("float_top")
    assert MEASUREMENT.value == data.get("value")


# Return the test image from the camera
@mock.patch(
    "homeassistant.components.demo.camera.Path.read_bytes",
    return_value=image_for_test(),
)
async def test_state(mock_camera_read, hass: HomeAssistant) -> None:
    """Verify the state makes sense after processing an image."""
    with indicam_client_mock():
        await setup_indicam(hass)
        await image_processing_scan(hass)
        await hass.async_block_till_done()
    state = hass.states.get("image_processing.indicam_test")
    assert float(state.state) == MEASUREMENT.value
    assert state.attributes.get(ATTR_GAUGE_MEASUREMENT) == MEASUREMENT


# Return the test image from the camera
@mock.patch(
    "homeassistant.components.demo.camera.Path.read_bytes",
    return_value=image_for_test(),
)
async def test_state_measurement_failed(mock_camera_read, hass: HomeAssistant) -> None:
    """Verify the state makes sense after processing an image."""
    with indicam_client_mock(None):
        await setup_indicam(hass)
        await image_processing_scan(hass)
        await hass.async_block_till_done()
    state = hass.states.get("image_processing.indicam_test")
    assert state.state == "unknown"
    assert state.attributes.get(ATTR_GAUGE_MEASUREMENT) is None
