# Local Docker setup work

Run the Django workspace and PostgreSQL in Compose while preserving the existing database volume. Support host Ollama by default and an optional containerized model server.

- [x] Add the application image, static serving, startup migrations, and health check.
- [x] Extend Compose and add local configuration/start instructions and optional Ollama files.
- [x] Verify model URL handling, access controls, the Docker build, and the PostgreSQL suite inside the image.
- [x] Back up existing data, start the local stack, and verify readiness, assets, and data preservation.

Keep native Python development available. Distributed workers and the scheduler are still future services.

Verified: 51 PostgreSQL tests pass natively and inside the Linux container; build and optional Compose configuration validation pass. Both services are healthy, assets and readiness return 200, non-root UID is 10001, the host model is reachable/installed, and existing counts remain unchanged. The database backup is Git/build-ignored. The existing port 8000 was occupied, so the local app uses 8001. PostgreSQL's original publication was retained to avoid a Windows bind conflict. Optional containerized Ollama and GPU inference were not started.
