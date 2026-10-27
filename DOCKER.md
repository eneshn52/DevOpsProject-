# Running the workspace in Docker

The default Compose stack runs the Django application (Gunicorn plus WhiteNoise static serving) and PostgreSQL. Existing Windows Ollama remains available through `host.docker.internal`. Frontend templates, CSS, and JavaScript are part of the application image; a separate frontend container is unnecessary.

An optional override also runs Ollama in Docker. Future schedulers and remote worker runtimes are not implemented yet, so they are not included as pretend services.

## Start on Windows

Use Docker Desktop with Linux containers. From the project directory in PowerShell:

```powershell
.\docker\start.ps1
```

The script creates `.env.docker` once, with a generated Django secret, and runs `docker compose --env-file .env.docker up --build -d --wait`. It preserves an existing configuration. To initialize configuration without starting containers:

```powershell
.\docker\start.ps1 -InitializeOnly
```

The application is available at **http://127.0.0.1:8000/**. PostgreSQL retains its existing host port 5432 publication; `DB_BIND_ADDRESS` defaults to `0.0.0.0` to preserve the existing Windows Docker mapping. It can be set to `127.0.0.1` where supported. Change `WEB_PORT` or `DB_PORT` in `.env.docker` if a port is already occupied. Django always connects to `db:5432` inside Compose, independently of the published database port.

The image installs `requirements-docker.txt`, collects static files during the build, and runs as an unprivileged user. Startup waits for PostgreSQL health, applies pending migrations, and launches Gunicorn. The `/health/` readiness endpoint checks database connectivity. Changes to application code require rebuilding the image.

On the verified installation, `.env.docker` uses `WEB_PORT=8001` because another process already occupied port 8000. Its application URL is **http://127.0.0.1:8001/**; new configurations still default to port 8000.

## Sign in

Container requests reach Django through Docker networking, so the Docker settings require a staff session rather than using the native loopback bypass. Existing staff accounts are preserved in PostgreSQL. If needed, create an administrator interactively:

```powershell
docker compose --env-file .env.docker exec web python manage.py createsuperuser
```

Sign in at **http://127.0.0.1:8000/admin/login/?next=/**. No default administrator password is created or printed. The workspace then uses your session and normal CSRF protection.

## Preserve the existing database

The existing volume is `devopsproject-_postgres_data`; `.env.docker` explicitly selects it through `POSTGRES_VOLUME_NAME`. The Compose project name remains `devopsproject-` and the database container remains `devops_postgres`. Rebuilding or replacing the application does not replace database contents. `docker compose down` keeps named volumes; adding `-v` removes Compose-managed volumes and must not be used to preserve this database.

For a separate installation, use a different `POSTGRES_VOLUME_NAME` and appropriate free ports. The `DB_NAME`, `DB_USER`, and `DB_PASSWORD` defaults match the existing local database. Changing initialization variables does not change credentials inside an already initialized PostgreSQL volume.

Back up before applying future schema changes. These commands write a PostgreSQL custom-format dump without sending binary output through PowerShell text redirection:

```powershell
New-Item -ItemType Directory -Path backups -Force
docker compose --env-file .env.docker exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/workspace-backup.dump'
docker compose --env-file .env.docker cp db:/tmp/workspace-backup.dump backups/workspace-backup.dump
```

Backups, local environment files, SQLite files, and Git metadata are excluded from image builds. `.env.docker` and backups are also ignored by Git. `.env.docker.example` documents configuration without holding a real generated secret. Native Django continues to use its original environment-variable configuration and does not automatically load either environment file.

## Models on the Windows host or remote computers

Default model settings inside the application:

```text
OLLAMA_DEFAULT_URL=http://host.docker.internal:11434
OLLAMA_LOOPBACK_HOST=host.docker.internal
```

Saved model connections using `localhost`, `127.0.0.1`, or `::1` are translated at request time to the configured loopback host, preserving the port and base path. Database records remain unchanged, so the same saved connections still work when running Django directly on Windows. LAN addresses such as `http://192.168.1.20:11434` are left unchanged.

Ollama must be running and reachable from Docker Desktop. Start the installed Windows app or run `ollama serve` before using chat or planning; the Compose startup script does not start a Windows process. A health check for the web service verifies Django/PostgreSQL readiness, not model availability. The frontend still distinguishes saved model configuration from verified remote worker connectivity.

Docker documents `host.docker.internal` for reaching host services: [Docker Desktop networking](https://docs.docker.com/desktop/features/networking/networking-how-tos/).

## Optional: run Ollama in Docker too

The optional service has its own persistent model volume and is reachable as `http://ollama:11434` from Django. It does not publish another host port or copy existing Windows model files automatically.

CPU configuration:

```powershell
docker compose --env-file .env.docker -f docker-compose.yml -f compose.ollama.yml up --build -d --wait
docker compose --env-file .env.docker -f docker-compose.yml -f compose.ollama.yml exec ollama ollama pull YOUR_MODEL_IDENTIFIER
```

Replace `YOUR_MODEL_IDENTIFIER` with the model configured as your master. Model downloads may be large and were not started by the default setup. Use the same `-f` arguments for subsequent commands managing this optional stack. Existing loopback model connections resolve to the Ollama service under this override; connections using custom host ports must be adjusted to its internal port 11434.

For an NVIDIA GPU with Docker Desktop's WSL 2 GPU support, add the GPU override:

```powershell
docker compose --env-file .env.docker -f docker-compose.yml -f compose.ollama.yml -f compose.ollama-gpu.yml up --build -d --wait
```

The GPU override requests `gpus: all`. It requires working GPU passthrough and does not add VRAM. See [Ollama Docker setup](https://docs.ollama.com/docker) and [Docker Desktop GPU prerequisites](https://docs.docker.com/desktop/features/gpu/). The default configuration continues using the existing host model runtime.

## Useful commands

```powershell
# Show readiness and startup errors.
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs --tail 100 web

# Rebuild after source changes.
docker compose --env-file .env.docker up --build -d --wait

# Run the PostgreSQL suite inside the application image.
docker compose --env-file .env.docker exec web python manage.py test django_modules --noinput

# Stop without removing data volumes.
docker compose --env-file .env.docker stop
```

The web application is bound to the local computer. PostgreSQL retains its previous host-port publication. The future remote worker APIs are separate from opening the browser application to the LAN; adding containers does not implement distributed orchestration.

## Verification recorded for this setup

The application image built successfully and both services reached healthy status. All 51 tests passed inside the Linux application container against a temporary PostgreSQL test database. Frontend CSS/JavaScript and readiness returned HTTP 200, the application ran as UID 10001, and Docker reached Windows Ollama with the configured master model installed. Project/task/message/plan counts remained 2/2/18/0 before and after startup. A database backup was saved as `backups/devops-before-docker-app-20261018.dump`. The browser sign-in page loaded correctly using collected admin assets.

Optional Ollama CPU/GPU Compose configurations were syntax-validated. Their images/model downloads and GPU inference were not run as part of the default host-Ollama setup.
