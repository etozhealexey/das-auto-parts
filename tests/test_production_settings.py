import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_requires_secure_configuration():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production")
    settings = Settings(
        _env_file=None,
        app_env="production",
        secret_key="x" * 48,
        session_https_only=True,
        allowed_hosts=["parts.example.com"],
    )
    assert settings.session_https_only


@pytest.mark.parametrize(
    "patch", [{"debug": True}, {"allowed_hosts": ["*"]}, {"secret_key": "short"}]
)
def test_production_rejects_unsafe_configuration(patch):
    values = {
        "app_env": "production",
        "secret_key": "x" * 48,
        "session_https_only": True,
        "allowed_hosts": ["parts.example.com"],
    }
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **(values | patch))
