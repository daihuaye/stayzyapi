import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize(
    ('environment', 'apple_environment'),
    [('production', 'Production'), ('staging', 'Sandbox')],
)
def test_deployed_settings_require_sendgrid_key_but_no_template(
    monkeypatch, environment, apple_environment,
):
    monkeypatch.delenv('STAYZY_SENDGRID_ADMIN_RESET_TEMPLATE_ID', raising=False)
    values = dict(
        environment=environment,
        apple_environment=apple_environment,
        database_url='postgresql://test:test@localhost/test',
        jwt_private_key='synthetic',
        jwt_public_key='synthetic',
        sendgrid_api_key='synthetic',
        bucket='synthetic',
        bucket_access_key_id='synthetic',
        bucket_secret_access_key='synthetic',
        apple_api_key_id='synthetic',
        apple_api_issuer_id='synthetic',
        apple_api_private_key='synthetic',
        apple_team_id='synthetic',
        apple_app_id=1,
        apple_root_certificate_paths=['synthetic'],
    )
    settings = Settings(_env_file=None, **values)
    assert settings.environment == environment

    values['sendgrid_api_key'] = None
    with pytest.raises(ValidationError, match='Missing production settings: sendgrid_api_key'):
        Settings(_env_file=None, **values)
