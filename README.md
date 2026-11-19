# WEB AI ASSISTANT — Infraquantum

Asistente conversacional para sustituir las FAQ tradicionales de una web empresarial.

## Arquitectura MVP
- FastAPI como backend REST.
- Widget JavaScript insertable mediante una etiqueta `<script>`.
- Panel administrativo protegido por token Bearer.
- FAQ y fuentes documentales almacenadas en SQLite para desarrollo.
- Ingesta inicial de páginas web y archivos TXT, MD y HTML.
- OpenAI API opcional para generar respuestas usando contexto recuperado.
- Preparado para desplegar en Render (Frankfurt).

## Instalación local
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```
Abrir `/admin`, `/docs` y `/health`.

## Widget
```html
<script src="https://TU-SERVICIO.onrender.com/widget.js"
 data-api-base="https://TU-SERVICIO.onrender.com"
 data-assistant-id="default" defer></script>
```

## Seguridad y producción
No publiques claves privadas. Configura `ADMIN_API_KEY`, `OPENAI_API_KEY` y `ALLOWED_ORIGINS` como variables privadas del servicio. Este MVP requiere endurecimiento antes de producción: autenticación con sesiones/roles, rate limiting, protección SSRF en el crawler, base de datos persistente Postgres, pgvector, auditoría y controles RGPD. SQLite local no es almacenamiento persistente adecuado para un despliegue efímero.

## Estructura
- `app/main.py`: API, chat, FAQ, ingesta y analítica.
- `app/db.py`: esquema de persistencia local.
- `static/widget.js`: widget público.
- `static/admin.html`: panel administrativo inicial.
- `render.yaml`: Blueprint de Render.
- `knowledge/`: documentación de ejemplo y pautas.
