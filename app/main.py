import os
import re
import secrets
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv()

from app.db import connect, init_db

app = FastAPI(title="WEB AI ASSISTANT API", version="1.1.0")
configured_origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "").split(",") if x.strip()]
# The public chat widget is designed for third-party sites. No cookies are used;
# admin APIs still require a high-entropy Bearer key.
app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_origins or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_CHUNKS = max(1, min(int(os.getenv("MAX_CONTEXT_CHUNKS", "5")), 12))
MAX_UPLOAD_BYTES = 5_000_000


@app.on_event("startup")
def startup():
    init_db()


def admin_auth(authorization: str = Header(default="")):
    expected = os.getenv("ADMIN_API_KEY", "")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "Credenciales administrativas inválidas")


class FAQIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    answer: str = Field(min_length=2, max_length=10000)
    active: bool = True


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(min_length=8, max_length=100)
    assistant_id: str = Field(default="default", min_length=1, max_length=80)


def clean_text(value: str) -> str:
    return re.sub(r"\\s+", " ", value).strip()


def split_chunks(value: str, size=1100, overlap=140):
    value = clean_text(value)
    chunks, start = [], 0
    while start < len(value):
        end = min(len(value), start + size)
        chunks.append(value[start:end])
        if end == len(value):
            break
        start = end - overlap
    return chunks


def add_source(kind, locator, title, content):
    chunks = split_chunks(content)
    with connect() as con:
        cur = con.execute(
            "INSERT INTO sources(kind,locator,title,status) VALUES(?,?,?,?)",
            (kind, locator, title, "indexed" if chunks else "empty"),
        )
        source_id = cur.lastrowid
        con.executemany(
            "INSERT INTO chunks(source_id,title,content) VALUES(?,?,?)",
            [(source_id, title, chunk) for chunk in chunks],
        )
    return {"id": source_id, "chunks": len(chunks), "status": "indexed" if chunks else "empty"}


def retrieve(query):
    terms = [t.lower() for t in re.findall(r"\\w+", query) if len(t) > 2][:12]
    if not terms:
        return []
    with connect() as con:
        faqs = con.execute("SELECT question,answer FROM faqs WHERE active=1").fetchall()
        chunks = con.execute("SELECT title,content FROM chunks").fetchall()
    scored = []
    for faq in faqs:
        text = f"Pregunta: {faq['question']} Respuesta: {faq['answer']}"
        score = sum(text.lower().count(term) for term in terms)
        if score:
            scored.append((score, text))
    for chunk in chunks:
        text = chunk["content"]
        score = sum(text.lower().count(term) for term in terms)
        if score:
            scored.append((score, f"{chunk['title'] or 'Documento'}: {text}"))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:MAX_CHUNKS]]


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "web-ai-assistant",
        "version": "1.1.0",
        "storage": "sqlite_ephemeral_unless_persistent_disk_configured",
    }


@app.get("/widget.js")
def widget():
    return FileResponse(STATIC / "widget.js", media_type="application/javascript")


@app.get("/admin")
def admin():
    return FileResponse(STATIC / "admin.html")


@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    context = retrieve(body.message)
    if not context:
        answer = (
            "No encuentro información suficiente en la documentación disponible "
            "para responder con seguridad. Puedo derivar tu consulta al equipo de atención."
        )
    else:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key:
            try:
                client = OpenAI(api_key=api_key, timeout=20.0, max_retries=1)
                system_prompt = (
                    "Eres el asistente oficial de una empresa. Responde en el idioma del usuario. "
                    "Usa exclusivamente el CONTEXTO para afirmar datos de la empresa. "
                    "Si falta información, dilo claramente y no inventes. Sé conciso.\n\n"
                    + "CONTEXTO:\n" + "\n---\n".join(context)
                )
                result = client.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": body.message},
                    ],
                    temperature=0.2,
                )
                answer = result.choices[0].message.content or "No he podido generar una respuesta."
            except Exception:
                raise HTTPException(502, "El proveedor de IA no está disponible temporalmente.")
        else:
            answer = "He encontrado esta información en la base de conocimiento:\n\n" + "\n\n".join(context)

    with connect() as con:
        con.executemany(
            "INSERT INTO conversations(session_id,role,content) VALUES(?,?,?)",
            [(body.session_id, "user", body.message), (body.session_id, "assistant", answer)],
        )
    return {
        "answer": answer,
        "sources": ["base de conocimiento"] if context else [],
        "handoff": not bool(context),
    }


@app.get("/admin/api/faqs", dependencies=[Depends(admin_auth)])
def list_faqs():
    with connect() as con:
        return [dict(row) for row in con.execute("SELECT * FROM faqs ORDER BY id DESC")]


@app.post("/admin/api/faqs", dependencies=[Depends(admin_auth)])
def create_faq(body: FAQIn):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO faqs(question,answer,active) VALUES(?,?,?)",
            (body.question, body.answer, int(body.active)),
        )
        return {"id": cur.lastrowid, "created": True}


@app.delete("/admin/api/faqs/{faq_id}", dependencies=[Depends(admin_auth)])
def delete_faq(faq_id: int):
    with connect() as con:
        cur = con.execute("DELETE FROM faqs WHERE id=?", (faq_id,))
        if not cur.rowcount:
            raise HTTPException(404, "FAQ no encontrada")
    return {"deleted": True}


@app.get("/admin/api/sources", dependencies=[Depends(admin_auth)])
def list_sources():
    with connect() as con:
        return [dict(row) for row in con.execute("SELECT * FROM sources ORDER BY id DESC")]


@app.post("/admin/api/ingest/url", dependencies=[Depends(admin_auth)])
def ingest_url_disabled():
    # Deliberately disabled until DNS pinning, redirect validation and private-IP
    # protections are implemented and security-tested (SSRF prevention).
    raise HTTPException(501, "La importación por URL está temporalmente deshabilitada por seguridad.")


@app.post("/admin/api/ingest/file", dependencies=[Depends(admin_auth)])
async def ingest_file(file: UploadFile = File(...)):
    filename = (file.filename or "upload.txt").lower()
    if not filename.endswith((".txt", ".md", ".html")):
        raise HTTPException(415, "Formatos admitidos: TXT, MD y HTML")
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Archivo superior a 5 MB")
    content = raw.decode("utf-8", errors="replace")
    if filename.endswith(".html"):
        content = BeautifulSoup(content, "html.parser").get_text(" ", strip=True)
    return add_source("file", filename, file.filename or filename, content)


@app.get("/admin/api/analytics", dependencies=[Depends(admin_auth)])
def analytics():
    with connect() as con:
        return {
            "faqs": con.execute("SELECT COUNT(*) FROM faqs").fetchone()[0],
            "sources": con.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
            "chunks": con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "messages": con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
        }
