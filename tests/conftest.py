import sys
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    """Synthetic tests must never contact a phone, run an external command, or use the network."""
    from wamove.ios import device

    def forbidden(*args, **kwargs):
        raise AssertionError("external operations are forbidden in offline tests")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    # Exercise portable copying and optional-thumbnail fallback independently
    # of the host's installed tools. Tool-specific tests inject their own fakes.
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda command, *args, **kwargs:
                        None if command in ("cp", "ffmpeg") else which(command, *args, **kwargs))
    for name in ("phone", "backup", "restore"):
        monkeypatch.setattr(device, name, forbidden)
