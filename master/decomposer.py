import json
import httpx

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3:14b"
MAX_CHARS = 12000  # Sprint 1 için basit sınır, uzun belge bölme FR-3'ün işi

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
                "required": ["type", "title", "description", "inputs", "expected_outputs"],
            },
        }
    },
    "required": ["tasks"],
}

SYSTEM_PROMPT = f"""Sen bir yazılım projesi planlayıcısısın.
Sana verilen gereksinim dökümanını küçük, bağımsız programlama görevlerine ayır.
Her görev için şunları ver:
- type: {", ".join(TASK_TYPES)} değerlerinden biri
- title: kısa başlık
- description: görevin ne yapacağı, 1-3 cümle
- inputs: görevin ihtiyaç duyduğu girdiler (örn. "veritabanı şeması")
- expected_outputs: görevin üreteceği çıktılar (örn. "models.py dosyası")
Görevler somut ve tek bir ajanın yapabileceği büyüklükte olsun.
Toplam 6 ile 15 görev üret. Yalnızca JSON döndür."""


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
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }
    response = httpx.post(OLLAMA_URL, json=payload, timeout=600)
    response.raise_for_status()
    content = response.json()["message"]["content"]
    return json.loads(content)["tasks"]
