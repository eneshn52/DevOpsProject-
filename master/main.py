import io
from pathlib import Path
import tasks_db
from decomposer import decompose

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pypdf import PdfReader

import db

app = FastAPI(title="Master Agent")
db.init()

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB


def extract_text(filename, data):
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Desteklenmeyen dosya türü: {ext}. İzin verilenler: .txt, .md, .pdf")
    if ext == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:
            raise HTTPException(400, "PDF okunamadı (bozuk veya şifreli olabilir).")
    return data.decode("utf-8", errors="replace")


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.post("/api/projects")
async def submit_project(
    name: str = Form(...),
    text: str = Form(""),
    file: UploadFile | None = File(None),
):
    requirements = text.strip()

    if file is not None and file.filename:
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, "Dosya 5 MB'dan büyük olamaz.")
        requirements = extract_text(file.filename, data).strip()

    if not name.strip():
        raise HTTPException(400, "Proje adı boş olamaz.")
    if not requirements:
        raise HTTPException(400, "Gereksinim metni boş (metin yapıştırın veya dosya yükleyin).")

    project_id = db.create_project(name.strip(), requirements)
    return {"id": project_id, "name": name.strip(), "status": "submitted", "characters": len(requirements)}


@app.get("/api/projects")
def list_projects():
    return db.list_projects()


@app.get("/api/projects/{project_id}")
def get_project(project_id: int):
    project = db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Proje bulunamadı.")
    return project

tasks_db.init()


@app.post("/api/projects/{project_id}/decompose")
def decompose_project(project_id: int):
    project = db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Proje bulunamadı.")
    try:
        tasks = decompose(project["requirements"])
    except Exception as e:
        raise HTTPException(502, f"Model hatası: {e}")
    tasks_db.replace_tasks(project_id, tasks)
    return {"project_id": project_id, "task_count": len(tasks), "tasks": tasks_db.get_tasks(project_id)}


@app.get("/api/projects/{project_id}/tasks")
def get_project_tasks(project_id: int):
    return tasks_db.get_tasks(project_id)
