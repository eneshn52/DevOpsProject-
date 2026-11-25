"""Disposable loopback development master for FR-21."""
from .settings import *  # noqa: F403

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'fr21-local.sqlite3'}}
WORKSPACE_ALLOW_LOCAL_ACCESS = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1']
