FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=django_settings.docker

WORKDIR /app
COPY requirements.txt requirements-docker.txt ./
RUN pip install --no-cache-dir -r requirements-docker.txt \
    && useradd --create-home --uid 10001 workspace \
    && chown workspace:workspace /app

COPY --chown=workspace:workspace manage.py gunicorn.conf.py ./
COPY --chown=workspace:workspace django_settings ./django_settings
COPY --chown=workspace:workspace django_modules ./django_modules
COPY --chown=workspace:workspace templates ./templates
COPY --chown=workspace:workspace docker/start.py ./docker/start.py

USER workspace
RUN USE_SQLITE=True python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["python", "docker/start.py"]
