import pytest
from fastapi.testclient import TestClient

from letssing.api.app import create_app
from letssing.config import ConfigManager, MemoryStore
from letssing.playlist import PlaylistRepository


@pytest.fixture
def repo(tmp_path):
    return PlaylistRepository(tmp_path / "playlist.db")


@pytest.fixture
def config(tmp_path):
    return ConfigManager(tmp_path / "config.json", secrets=MemoryStore())


@pytest.fixture
def client(repo, config):
    return TestClient(create_app(config=config, playlist=repo))
