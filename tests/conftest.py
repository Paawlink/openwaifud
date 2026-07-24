"""Shared test fixtures."""

import pytest

from openwaifud.config import Config
from openwaifud.state.manager import StateManager


@pytest.fixture
def config():
    """Create a test config."""
    return Config(http_host="127.0.0.1", http_port=0)  # port 0 = random


@pytest.fixture
def state_manager():
    """Create a fresh StateManager."""
    return StateManager(queue_max_size=10)
