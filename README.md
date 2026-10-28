# DevOpsProject-
[Workspace API, PostgreSQL setup, and verification](WORKSPACE_API.md)

[Implemented features and future roadmap](PROJECT_STATUS.md)

[Docker setup and startup instructions](DOCKER.md)

Saved task planning is available from **Task plans** in the sidebar. Configure a master model, select a project, and enter a request to generate tasks with validated dependencies, expected outputs, and acceptance criteria. Plans are stored in PostgreSQL; automatic routing and execution are subsequent features.

CM6453: Distributed AI Software Development Platform

This project is a web-based software system based on a Distributed Multi-Agent Architecture, developed for the CM6453 Software Processes term project.



CM6453: Distributed AI Software Development Platform

This project is a web-based software system based on a Distributed Multi-Agent Architecture, developed for the CM6453 Software Processes term project.

 About the Project

The main goal of this platform is to build a system of AI agents capable of analyzing long software requirements entered by a user (e.g., "Develop an online library management system") and transforming them into a fully functional web application.

The target architecture uses a Master Agent to decompose requirements, evaluate available specialists, and propose task assignments. Saved task decomposition is implemented. Capability evaluation, automatic assignment, and distributed execution remain planned work; see the status document above for the current boundary.

 Technologies Used

Backend: Python and Django with same-origin JSON APIs.

Database: PostgreSQL (primary); SQLite is an explicit local development option.

Project Management: Trello, Scrum Methodology

Version Control: Git & GitHub

✅What We Have Done So Far

A short, plain description of each piece of the system that already works:

Workspace interface: A web UI with a sidebar, conversation area, agent panel, dark/light themes and keyboard shortcuts. Built with Django templates, plain JavaScript and CSS (no separate frontend build).

Projects (FR-2): Users can create and select projects. Each project stores its name, requirements, summary, creation date and status.

PostgreSQL persistence: Projects, conversations, messages, agents, API connections, task plans, tasks and their dependencies are stored on the server, so data survives a page reload or a different browser.

Master chat: The user talks to a configured master model (served by Ollama). Replies are saved, and repeating the same request does not generate it twice.

Agent configuration: Up to five specialist agents can be saved with a model, role, machine label and API connection. API keys stay on the server and are never returned to the browser.

Task decomposition (FR-11, FR-12): The master turns a project request into 1-20 structured tasks. Each task has a type, instructions, required capabilities, expected output and acceptance criteria. Dependencies between tasks are validated (no missing references, self-dependencies or cycles) and the plan is saved atomically.

Task classification (FR-13): The master selects a validated task type; Django assigns and stores the corresponding `coding`, `reasoning`, `context`, and `speed` weights. Saved plans and worker assignments expose `capabilityWeights`, and the FR-14 scheduler uses these stored weights. Migration 0008 backfills existing tasks without changing their legacy scalar `weights`, statuses, or dependencies. See [classification policy](WORKSPACE_API.md#task-classification-fr-13).

Live agent monitoring (FR-7): The agent panel shows, for every agent, its status (Idle / Busy / Offline), the task it is working on, its queue length (tasks assigned but not started), the last response time and the last heartbeat. The UI polls `GET /api/agents/status/` every 2 seconds, only while the panel is visible. Tests: `django_modules/test_agent_status.py`.

Docker runtime: A container image (Gunicorn, WhiteNoise) and a Docker Compose setup run Django together with PostgreSQL. Optional Ollama CPU/GPU overrides are included. See DOCKER.md.

Tests: Automated tests cover the workspace API, access control, request replay, planning validation, concurrency, migrations and agent status. Run them with `python manage.py test`.


Worker registration and heartbeats (FR-4, FR-6), capability scoring and benchmarks (FR-8, FR-9), automatic task assignment (FR-14), distributed task execution, automated testing/review of generated code, and integration of the results are planned for the next sprints. The monitoring panel will show real data once heartbeats and assignment exist. 


