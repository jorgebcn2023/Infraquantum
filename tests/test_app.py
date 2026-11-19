from fastapi.testclient import TestClient
import app.db as db
from app.main import app

def setup_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("ADMIN_API_KEY", "test-secret-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    db.init_db()
    return TestClient(app)

def test_public_pages_and_health(tmp_path, monkeypatch):
    client=setup_client(tmp_path,monkeypatch)
    assert client.get("/").status_code==200
    assert "WEB AI ASSISTANT" in client.get("/").text
    assert client.get("/widget.js").status_code==200
    assert client.get("/admin").status_code==200
    assert client.get("/health").json()["version"]=="1.1.0"

def test_admin_requires_bearer_key(tmp_path, monkeypatch):
    client=setup_client(tmp_path,monkeypatch)
    assert client.get("/admin/api/faqs").status_code==401
    assert client.get("/admin/api/faqs",headers={"Authorization":"Bearer test-secret-key"}).status_code==200

def test_faq_crud_and_chat(tmp_path, monkeypatch):
    client=setup_client(tmp_path,monkeypatch);headers={"Authorization":"Bearer test-secret-key"}
    created=client.post("/admin/api/faqs",headers=headers,json={"question":"¿Cuál es el horario de atención?","answer":"Atendemos de lunes a viernes."})
    assert created.status_code==200;faq_id=created.json()["id"]
    answer=client.post("/api/chat",json={"message":"¿Cuál es el horario de atención?","session_id":"session-test-001","assistant_id":"default"})
    assert answer.status_code==200 and "lunes a viernes" in answer.json()["answer"]
    assert client.delete(f"/admin/api/faqs/{faq_id}",headers=headers).status_code==200
    assert client.delete(f"/admin/api/faqs/{faq_id}",headers=headers).status_code==404

def test_url_ingestion_is_disabled(tmp_path, monkeypatch):
    client=setup_client(tmp_path,monkeypatch)
    response=client.post("/admin/api/ingest/url",headers={"Authorization":"Bearer test-secret-key"},json={"url":"https://example.com"})
    assert response.status_code==501
