"""The native recovery drill cannot be pointed at an existing/live database."""
import os
from pathlib import Path
import tempfile
from urllib.parse import urlencode

import pytest
from scripts import verify_postgres_restore as drill


@pytest.fixture
def private(monkeypatch):
    with tempfile.TemporaryDirectory(prefix='scpg-',dir='/tmp') as directory:
        root=Path(directory).resolve();(root/'.scrapper-postgres-fixture').touch();(root/'socket').mkdir(mode=0o700)
        monkeypatch.setenv('SCRAPPER_PG_TEST_ROOT',str(root))
        monkeypatch.setenv('DATABASE_URL','postgresql+psycopg2://scrapper_fixture@/scrapper_fixture?'+urlencode({'host':str(root/'socket')}))
        yield root


def test_marked_private_unix_fixture_can_be_identified_without_connecting(private):
    assert drill.private_fixture()==private


@pytest.mark.parametrize('changes',[
    'postgresql+psycopg2://owner:private-value@example.invalid/live',
    'postgresql+psycopg2://scrapper_fixture@/other?host=/tmp/other',
    'postgresql+psycopg2://scrapper_fixture@/scrapper_fixture?host=/tmp/live',
])
def test_other_hosts_databases_and_sockets_fail_before_any_connection(private,monkeypatch,changes):
    monkeypatch.setenv('DATABASE_URL',changes)
    monkeypatch.setattr(drill.psycopg2,'connect',lambda **kwargs:pytest.fail('Rejected context attempted a database connection'))
    with pytest.raises(RuntimeError,match='no other database') as error:drill.private_fixture()
    assert 'private-value' not in str(error.value)


def test_writable_or_symlinked_fixture_cannot_authorize_the_drill(private):
    private.chmod(0o777)
    try:
        with pytest.raises(RuntimeError,match='private marked'):drill.private_fixture()
    finally:private.chmod(0o700)
    (private/'socket').rmdir();(private/'other').mkdir();(private/'socket').symlink_to(private/'other',target_is_directory=True)
    with pytest.raises(RuntimeError,match='private marked'):drill.private_fixture()


def test_source_writes_and_non_fixture_database_names_fail_before_connection(private,monkeypatch):
    monkeypatch.setattr(drill.psycopg2,'connect',lambda **kwargs:pytest.fail('Unsafe context attempted a database connection'))
    with pytest.raises(RuntimeError,match='read-only'):drill.connect(private,drill.SOURCE,readonly=False)
    with pytest.raises(RuntimeError,match='Unexpected fixture'):drill.connect(private,'production',readonly=False)
    with pytest.raises(RuntimeError,match='Unexpected fixture role'):drill.connect(private,'scrapper_restore_fixture',readonly=False,user='production-owner')
