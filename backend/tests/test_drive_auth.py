"""Use only an explicitly configured existing owner grant; never grant scopes."""
import json
import pytest
from backend.app.drive_sync import get_drive_service,SCOPES


def test_existing_owner_oauth_is_preferred_without_service_account_fallback(monkeypatch):
    import google.oauth2.credentials
    import googleapiclient.discovery
    credentials=object();service=object();observed={}
    info={'type':'authorized_user','refresh_token':'synthetic-token','client_id':'synthetic','client_secret':'synthetic'}
    monkeypatch.setenv('GOOGLE_DRIVE_AUTHORIZED_USER_JSON',json.dumps(info))
    def load(value,scopes):
        observed.update(info=value,scopes=scopes);return credentials
    monkeypatch.setattr(google.oauth2.credentials.Credentials,'from_authorized_user_info',load)
    def build(api,version,credentials):
        assert api=='drive' and version=='v3'
        observed['credentials']=credentials
        return service
    monkeypatch.setattr(googleapiclient.discovery,'build',build)
    assert get_drive_service() is service
    assert observed=={'info':info,'scopes':SCOPES,'credentials':credentials}


def test_invalid_explicit_owner_connection_fails_without_silent_other_account(monkeypatch):
    monkeypatch.setenv('GOOGLE_DRIVE_AUTHORIZED_USER_JSON','{"refresh_token":"synthetic-credential"}')
    with pytest.raises(RuntimeError,match='configured owner Drive OAuth') as error:get_drive_service()
    assert 'synthetic-credential' not in str(error.value)
