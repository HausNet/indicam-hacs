"""The Indicator Camera Component."""

import logging
from dataclasses import dataclass
from typing import Optional

import indicam_client
import voluptuous as vol

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, INDICAM_URL, CONF_CLIENT_API_KEY, CONF_PATH_OUT

_LOGGER = logging.getLogger(__name__)

# The component configuration
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                # The IndiCam service API key
                vol.Required(CONF_CLIENT_API_KEY): cv.string,
                # The path where image snapshots and decorated images can be saved.
                vol.Optional(CONF_PATH_OUT): cv.path,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


@dataclass
class IndicamComponentState:
    """A class holding component configuration and state variables"""
    api_key: str
    api_client: indicam_client.IndiCamServiceClient
    out_path: Optional[str]


# The variable holding the platform state - used by internal modules
component_state: IndicamComponentState | None = None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """ Set up the Indicam component.

        Retrieves the authentication key from config, saves it in hass, and sets up
        a client connection.
    """
    # TODO: Remove the DOMAIN test after the image_processing platform has been removed
    if DOMAIN not in config:
        return True
    await setup_component_state(hass, config[DOMAIN][CONF_CLIENT_API_KEY], config[DOMAIN][CONF_PATH_OUT])
    return True


async def setup_component_state(hass: HomeAssistant, client_api_key: str, path: str):
    global component_state
    component_state = IndicamComponentState(
        api_key=client_api_key,
        api_client=await create_client(hass, client_api_key),
        out_path=path
    )


def get_component_state() -> IndicamComponentState | None:
    """Return the component state."""
    return component_state


async def create_client(hass: HomeAssistant, client_auth_key: str) -> indicam_client.IndiCamServiceClient:
    session = async_get_clientsession(hass)
    client = indicam_client.IndiCamServiceClient(session, INDICAM_URL, client_auth_key)
    connect_status = await client.test_connect()
    if connect_status == indicam_client.CONNECT_FAIL:
        raise ConfigEntryError(f"Connection failed trying to connect to client at {INDICAM_URL}")
    if connect_status == indicam_client.CONNECT_AUTH_FAIL:
        raise ConfigEntryAuthFailed("Authentication failed")
    return client
