"""Local E2E containment. Enabled before application imports by config.py."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import socket


class IsolationError(RuntimeError):
    pass


def validate_storage(data_dir: str, database_url: str, root: str) -> None:
    if any(os.environ.get(k) for k in ("RAILWAY_ENVIRONMENT", "VERCEL")):
        raise IsolationError("SAFE_E2E_MODE cannot run in a deployed environment")
    boundary = Path(root).resolve() if root else None
    if (not boundary or not (boundary / ".scrapper-e2e").is_file()
            or not Path(data_dir).resolve().is_relative_to(boundary)
            or Path(data_dir).resolve() == Path("/data")):
        raise IsolationError("SAFE_E2E_MODE requires an isolated root with .scrapper-e2e marker")
    if database_url and (not database_url.startswith("sqlite:///")
                         or not Path(database_url[len("sqlite:///"):]).resolve().is_relative_to(boundary)):
        raise IsolationError("E2E database must be SQLite inside SAFE_E2E_ROOT")


def install_network_guard() -> None:
    """Allow loopback only, including asynchronous connect_ex and DNS.

    The startup script additionally uses the OS sandbox for child processes.
    No cloud SDK should even be constructed; provider entry points also reject.
    """
    if getattr(socket, "_scrapper_e2e_guard", False):
        return
    def check(address):
        if isinstance(address, tuple):
            host = address[0]
            if host == "localhost":
                return
            try:
                if ipaddress.ip_address(host).is_loopback:
                    return
            except ValueError:
                pass
            raise IsolationError("SAFE_E2E_MODE blocked external network destination")
    for name in ("connect", "connect_ex"):
        original = getattr(socket.socket, name)
        def guarded(self, address, _original=original):
            check(address)
            return _original(self, address)
        setattr(socket.socket, name, guarded)
    getaddrinfo = socket.getaddrinfo
    def resolve(host, port, *args, **kwargs):
        if host is not None:
            check((host, port))
        return getaddrinfo(host, port, *args, **kwargs)
    socket.getaddrinfo = resolve
    socket._scrapper_e2e_guard = True


def deny_live(operation: str) -> None:
    from .config import settings
    if settings.safe_e2e_mode:
        raise IsolationError(f"SAFE_E2E_MODE blocked real {operation}; use a fixture adapter")
