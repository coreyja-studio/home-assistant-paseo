"""Coordinator for Paseo push updates."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import PaseoClient
from .const import DOMAIN
from .models import PaseoSnapshot


class PaseoCoordinator(DataUpdateCoordinator[PaseoSnapshot]):
    """Publish Paseo's local-push snapshots to entities."""

    def __init__(self, hass: HomeAssistant, client: PaseoClient) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, logger=logging.getLogger(__name__), name=DOMAIN)
        self.client = client

    async def async_start(self) -> None:
        """Connect to Paseo and publish the initial snapshot."""
        snapshot = await self.client.async_start()
        self.async_set_updated_data(snapshot)

    async def async_stop(self) -> None:
        """Disconnect from Paseo."""
        await self.client.async_stop()

    def async_receive_snapshot(self, snapshot: PaseoSnapshot) -> None:
        """Receive a push update from the client."""
        self.async_set_updated_data(snapshot)
