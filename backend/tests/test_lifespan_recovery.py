"""Startup must fail closed; successful serving must still mark interrupted jobs."""
from fastapi.testclient import TestClient
import pytest
from backend.app import main


def test_http_lifespan_initializes_once_and_marks_shutdown_once(monkeypatch):
    events=[]
    monkeypatch.setattr(main.settings,'safe_e2e_mode',True)
    monkeypatch.setattr(main,'init_db',lambda:events.append('init'))
    monkeypatch.setattr(main.resume,'mark_shutdown',lambda:events.append('shutdown'))
    with TestClient(main.app,headers={'x-access-token':'test-token'}) as client:
        assert events==['init']
        assert client.get('/api/stats').status_code==200
        assert events==['init']
    assert events==['init','shutdown']


def test_failed_pre_migration_initialization_never_enters_serving_or_shutdown(monkeypatch):
    events=[]
    def init():raise RuntimeError('Pre-migration snapshot was not verified')
    monkeypatch.setattr(main,'init_db',init)
    monkeypatch.setattr(main.resume,'mark_shutdown',lambda:events.append('unexpected-shutdown'))
    with pytest.raises(RuntimeError,match='snapshot was not verified'):
        with TestClient(main.app):pytest.fail('App served after initialization failure')
    assert events==[]
