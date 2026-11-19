# WEB AI ASSISTANT — Infraquantum

Prototipo empresarial V1.1 con página pública, widget insertable, panel protegido, FAQ y carga documental.

## Instalación
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## Variables
- `ADMIN_API_KEY`: clave aleatoria larga, obligatoria para operaciones administrativas.
- `OPENAI_API_KEY`: opcional.
- `ALLOWED_ORIGINS`: dominios separados por comas. Para el sitio principal, configura `https://infraquantum.org` (sin ruta ni barra final). Añade `https://www.infraquantum.org` solo si también sirves la web desde ese host. No se usan cookies y las API admin requieren Bearer.
- `DATABASE_PATH`: ruta SQLite.

## Widget
```html
<script src="https://TU-SERVICIO.onrender.com/widget.js" data-api-base="https://TU-SERVICIO.onrender.com" data-assistant-id="default" defer></script>
```

## Rutas
`/`, `/health`, `/widget.js`, `/admin`, `POST /api/chat`, y `/admin/api/*` con Bearer.

La ingesta por URL está deshabilitada hasta implementar mitigaciones SSRF verificables. Carga de TXT/MD/HTML limitada a 5 MB.

## Persistencia y producción
Render Free no tiene disco persistente por defecto. SQLite puede perderse al reiniciar o desplegar. No cargues información empresarial crítica hasta configurar almacenamiento duradero (PostgreSQL recomendado). La búsqueda actual es lexical, no semántica. Antes de producción se necesitan rate limiting, sesiones/roles, auditoría, controles RGPD, límites de consumo y pruebas de seguridad.

Ejecuta `pytest -q` para pruebas de regresión.
