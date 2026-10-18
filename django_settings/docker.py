"""Container settings; native development continues to use settings.py."""
import os

from .settings import *  # noqa: F403

DEBUG = False
SECRET_KEY = os.environ['DJANGO_SECRET_KEY'] if 'DJANGO_SECRET_KEY' in os.environ else SECRET_KEY  # noqa: F405
ALLOWED_HOSTS = [host.strip() for host in os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1]').split(',') if host.strip()]
WORKSPACE_ALLOW_LOCAL_ACCESS = False
MIDDLEWARE = [MIDDLEWARE[0], 'whitenoise.middleware.WhiteNoiseMiddleware', *MIDDLEWARE[1:]]  # noqa: F405
STATIC_ROOT = BASE_DIR / 'staticfiles'  # noqa: F405
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
