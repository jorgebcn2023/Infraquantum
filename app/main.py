import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Header, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from openai import OpenAI

load_dotenv()

from app.db import connect, init_db
app = FastAPI(title="WEB AI ASSISTANT API", version="1.0.0")
origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=origins, allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"]
)
STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_CHUNKS = int(os.getenv("MAX_CONTEXT_CHUNKS", "5"))

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

class URLIn(BaseModel):
    url: str

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(min_length=8, max_length=100)
    assistant_id: str = "default"

def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()

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
            (kind, locator, title, "indexed" if chunks else "empty")
        )
        source_id = cur.lastrowid
        con.executemany(
            "INSERT INTO chunks(source_id,title,content) VALUES(?,?,?)",
            [(source_id, title, chunk) for chunk in chunks]
        )
    return {"id": source_id, "chunks": len(chunks), "status": "indexed" if chunks else "empty"}

def retrieve(query):
    terms = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 2][:12]
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
        score = sum(chunk["content"].lower().count(term) for term in terms)
        if score:
            scored.append((score, f"{chunk['title'] or 'Documento'}: {chunk['content']}"))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:MAX_CHUNKS]]

@app.get("/health")
def health():
    return {"status": "ok", "service": "web-ai-assistant", "version": "1.0.0"}

@app.get("/widget.js")
def widget():
    return FileResponse(STATIC / "widget.js", media_type="application/javascript")

@app.get("/admin")
def admin():
    return FileResponse(STATIC / "admin.html")

@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    origin = request.headers.get("origin", "")
    if origins and origin and origin not in origins:
        raise HTTPException(403, "Origen no autorizado")
    context = retrieve(body.message)
    if not context:
        answer = ("No encuentro información suficiente en la documentación disponible "
                  "para responder con seguridad. Puedo derivar tu consulta al equipo de atención.")
    else:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key:
            client = OpenAI(api_key=api_key)
            system_prompt = (
                "Eres el asistente oficial de una empresa. Responde en el idioma del usuario. "
                "Usa exclusivamente el CONTEXTO para afirmar datos de la empresa. "
                "Si falta información, dilo claramente y no inventes. Sé conciso.\\n\\n"
                + "CONTEXTO:\\n" + "\n---\n".join(context)
            )
            result = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": body.message}
                ],
                temperature=0.2
            )
            answer = result.choices[0].message.content or "No he podido generar una respuesta."
        else:
            answer = "He encontrado esta información en la base de conocimiento:\\n\\n" + "\n\n".join(context)
    with connect() as con:
        con.executemany(
            "INSERT INTO conversations(session_id,role,content) VALUES(?,?,?)",
            [(body.session_id, "user", body.message), (body.session_id, "assistant", answer)]
        )
    return {"answer": answer, "sources": ["base de conocimiento"] if context else [],
            "handoff": not bool(context)}

@app.get("/admin/api/faqs", dependencies=[Depends(admin_auth)])
def list_faqs():
    with connect() as con:
        return [dict(row) for row in con.execute("SELECT * FROM faqs ORDER BY id DESC")]

@app.post("/admin/api/faqs", dependencies=[Depends(admin_auth)])
def create_faq(body: FAQIn):
    with connect() as con:
        cur = con.execute(
            "INSERT INTO faqs(question,answer,active) VALUES(?,?,?)",
            (body.question, body.answer, int(body.active))
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
def ingest_url(body: URLIn):
    parsed = urlparse(body.url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(400, "URL no válida")
    # Production hardening required: SSRF-safe DNS/IP validation and redirect validation.
    try:
        with httpx.Client(timeout=15, follow_redirects=True,
                          headers={"User-Agent": "WebAIAssistantBot/1.0"}) as client:
            response = client.get(body.url)
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        title = clean_text(soup.title.get_text()) if soup.title else parsed.netloc
        content = clean_text(soup.get_text(" ", strip=True))
        return {"title": title, "url": str(response.url),
                **add_source("url", str(response.url), title, content)}
    except Exception as exc:
        raise HTTPException(422, f"No se pudo indexar la URL: {type(exc).__name__}")

@app.post("/admin/api/ingest/file", dependencies=[Depends(admin_auth)])
async def ingest_file(file: UploadFile = File(...)):
    filename = (file.filename or "upload.txt").lower()
    if not filename.endswith((".txt", ".md", ".html")):
        raise HTTPException(415, "Formatos iniciales admitidos: TXT, MD y HTML")
    raw = await file.read()
    if len(raw) > 5_000_000:
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
            "messages": con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        }
