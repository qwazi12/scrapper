import subprocess
import sys

import pytest

from backend.app.config import Settings
from backend.app.e2e_safety import IsolationError


def test_isolation_requires_marker_and_local_database(tmp_path):
    with pytest.raises(IsolationError): Settings(safe_e2e_mode=True, safe_e2e_root=str(tmp_path), data_dir=str(tmp_path))
    (tmp_path / ".scrapper-e2e").touch()
    s = Settings(safe_e2e_mode=True, safe_e2e_root=str(tmp_path), data_dir=str(tmp_path))
    assert s.safe_e2e_mode
    with pytest.raises(IsolationError):
        Settings(safe_e2e_mode=True, safe_e2e_root=str(tmp_path), data_dir=str(tmp_path), database_url="postgresql://live/db")
    with pytest.raises(IsolationError):
        Settings(safe_e2e_mode=True, safe_e2e_root=str(tmp_path), data_dir="/data")


def test_network_guard_rejects_external_dns_and_async_connect():
    # Child process avoids leaving test-global socket patches behind.
    code = '''from backend.app.e2e_safety import install_network_guard, IsolationError
import socket
install_network_guard()
for operation in (lambda: socket.getaddrinfo("example.com",443), lambda: socket.socket().connect_ex(("1.1.1.1",443))):
    try: operation()
    except IsolationError: pass
    else: raise AssertionError("escaped E2E isolation")
'''
    subprocess.run([sys.executable, "-c", code], check=True, timeout=10)
