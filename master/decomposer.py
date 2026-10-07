import json
import httpx

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3:14b"
MAX_CHARS = 12000

TASK_TYPES = [
    "requirements_analysis", "architecture", "database",
    "backend", "frontend", "testing", "integration", "documentation",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": TASK_TYPES},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "inputs": {"type": "array", "items": {"type": "string"}},
                    "expected_outputs": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "type",
                    "title",
                    "description",
                    "inputs",
                    "expected_outputs"
                ],
            },
        }
    },
    "required": ["tasks"],
}

SYSTEM_PROMPT = f"""You are a software project planner.
Break down the given requirements document into small, independent programming tasks.

For each task, provide:
- type: one of the following values: {", ".join(TASK_TYPES)}
- title: a short title
- description: what the task should accomplish, in 1-3 sentences
- inputs: the inputs required by the task (e.g. "database schema")
- expected_outputs: the outputs the task should produce (e.g. "models.py file")

Tasks should be concrete and small enough to be completed by a single agent.
Generate between 6 and 15 tasks in total.
Return only JSON."""


def decompose(requirements):
    text = requirements[:MAX_CHARS]

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "think": False,
        "format": SCHEMA,
        "options": {
            "temperature": 0.2,
            "num_ctx": 8192
        },
    }

    response = httpx.post(
        OLLAMA_URL,
        json=payload,
        timeout=600
    )

    response.raise_for_status()

    content = response.json()["message"]["content"]

    return json.loads(content)["tasks"]
