"""The Indicator Camera Component."""

import logging
from dataclasses import dataclass
from typing import Optional

import indicam_client

from homeassistant.const import Platform
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import INDICAM_URL, CONF_CLIENT_API_KEY, CONF_PATH_OUT, DOMAIN

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
    """ Set up the config entry by calling async_setup.

        1. Creates the component state, and stores it in the config entry for reference.
        2. Forwards to the sensor's entry setup for it to set up the sensor.
        3. Then, registers an option update listener, and stores the unsubscribe callback for reference.
    """
    entry.runtime_data = IndiCamComponentState(
        api_key=entry.data[CONF_CLIENT_API_KEY],
        api_client=await create_client(hass, entry.data[CONF_CLIENT_API_KEY]),
        out_path=entry.data[CONF_PATH_OUT]
    )
    await hass.config_entries.async_forward_entry_setups(entry, ['sensor'])
    entry.add_update_listener(options_update_listener)
    return True


async def create_client(hass: HomeAssistant, client_auth_key: str) -> indicam_client.IndiCamServiceClient:
    """ Creates an async http client to the IndiCam service, and tests the connection."""
    session = async_get_clientsession(hass)
    client = indicam_client.IndiCamServiceClient(session, INDICAM_URL, client_auth_key)
    await test_client_connect(client)
    return client


async def test_client_connect(client: indicam_client.IndiCamServiceClient):
    """ Test the connection to the service with a client. """
    connect_status = await client.test_connect()
    if connect_status == indicam_client.CONNECT_FAIL:
        raise ConfigEntryError(f"Connection failed trying to connect to client at {INDICAM_URL}")
    if connect_status == indicam_client.CONNECT_AUTH_FAIL:
        raise ConfigEntryAuthFailed("Authentication failed")


async def options_update_listener(hass: HomeAssistant, config_entry: ConfigEntry):
    """Handle options update."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the config entry, for use after options are updated."""
    return await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR,])
