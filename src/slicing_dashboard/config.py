"""
Application configuration.

Loads settings from environment variables / .env file using pydantic-settings.
Credentials are never hardcoded — they must come from the environment.
"""
from __future__ import annotations
from pathlib import Path
from typing import ClassVar
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / 'data'
RAW_DIR = DATA_DIR / 'raw'
PROCESSED_DIR = DATA_DIR / 'processed'
REPORTS_DIR = DATA_DIR / 'reports'
LOGS_DIR = PROJECT_ROOT / 'logs'


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / '.env'),
        env_file_encoding='utf-8', case_sensitive=False, extra='ignore')
    dashboard_url: str = Field(default='http://120.79.192.226/',
        description='Base URL of the video slicing dashboard')
    admin_username: str = Field(..., env='ADMIN_USERNAME')
    admin_password: str = Field(..., env='ADMIN_PASSWORD')
    database_url: str | None = Field(None, env='DATABASE_URL')
    timezone: str = Field(default='Asia/Kolkata', description=
        'Canonical timezone for date normalization')
    request_timeout: int = Field(default=30, description=
        'HTTP request timeout in seconds')
    max_retries: int = Field(default=3, description=
        'Maximum number of retry attempts for failed requests')
    user_mapping_path: str = Field(default='config/user_mapping.json',
        description=
        'Path to user mapping configuration file (relative to project root)')
    _DIRS: ClassVar[dict[str, Path]] = {'data': DATA_DIR, 'raw': RAW_DIR,
        'processed': PROCESSED_DIR, 'reports': REPORTS_DIR, 'logs': LOGS_DIR}

    def ensure_directories(self) ->None:
        """Create all required directories if they don't exist."""
        for dir_path in self._DIRS.values():
            dir_path.mkdir(parents=True, exist_ok=True)

    @property
    def has_credentials(self) ->bool:
        """Check if admin credentials are configured."""
        return bool(self.admin_username and self.admin_password)


def get_settings() ->Settings:
    """Get application settings (singleton-like via module-level cache)."""
    return Settings()
