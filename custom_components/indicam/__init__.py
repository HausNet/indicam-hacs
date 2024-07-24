"""The Indicator Camera Component."""

import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp
import indicam_client

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import INDICAM_URL, CONF_CLIENT_API_KEY, CONF_PATH_OUT

_LOGGER = logging.getLogger(__name__)

# The type of the integration data stored in the config entry
type IndiCamConfigEntry = ConfigEntry[IndiCamComponentState]


@dataclass
class IndiCamComponentState:
    """A class holding component configuration and state variables"""
    api_key: str
    api_client: indicam_client.IndiCamServiceClient
    out_path: Optional[str]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """ Set up the config entry by calling async_setup."""
    entry.runtime_data = IndiCamComponentState(
        api_key=entry.data[CONF_CLIENT_API_KEY],
        api_client=await create_client(hass, entry.data[CONF_CLIENT_API_KEY]),
        out_path=entry.data[CONF_PATH_OUT]
    )
    await hass.config_entries.async_forward_entry_setup(entry, 'sensor')
    return True


async def create_client(hass: HomeAssistant, client_auth_key: str) -> indicam_client.IndiCamServiceClient:
    """ Creates an async http client to the IndiCam service, and tests the connection."""
    session = async_get_clientsession(hass)
    client = indicam_client.IndiCamServiceClient(session, INDICAM_URL, client_auth_key)
    test_client_connect(client)
    return client


async def test_client_connect(client: indicam_client.IndiCamServiceClient):
    """ Test the connection to the service with a client. """
    connect_status = await client.test_connect()
    if connect_status == indicam_client.CONNECT_FAIL:
        raise ConfigEntryError(f"Connection failed trying to connect to client at {INDICAM_URL}")
    if connect_status == indicam_client.CONNECT_AUTH_FAIL:
        raise ConfigEntryAuthFailed("Authentication failed")
