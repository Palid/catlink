"""Pytest configuration and fixtures for CatLink tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

# Add repo root to path so custom_components.catlink can be imported
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

pytest_plugins = ("pytest_homeassistant_custom_component",)


@pytest.fixture
async def connect(socket_enabled: None):
    """Open scripted RTSP clients and close them after the test."""
    from .live_view_fakes import Client

    clients = []

    async def _connect(url: str) -> Client:
        """Open one client on the URL's host and port."""
        parts = urlsplit(url)
        client = Client(*await asyncio.open_connection(parts.hostname, parts.port))
        clients.append(client)
        return client

    yield _connect
    for client in clients:
        client.writer.close()
