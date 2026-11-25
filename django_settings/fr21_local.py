"""Disposable loopback development master for FR-21."""
from .settings import *  # noqa: F403
import os

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.getenv('FR21_DB_PATH') or BASE_DIR / 'fr21-local.sqlite3'}}
GENERATED_REPO_DIR = ''
WORKSPACE_ALLOW_LOCAL_ACCESS = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1']
