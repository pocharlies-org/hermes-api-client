# hermes-api-client

Cliente Python (httpx) del [api_server de Hermes](https://github.com/NousResearch/hermes-agent):
asegurar la sesión, leer el historial, lanzar un turno — por `chat/stream` o por `/v1/runs` — y
firmar/verificar los webhooks que Hermes entrega en `POST /p/<perfil>/webhooks/<ruta>`.

**Es el único sitio que sabe cómo se habla con Hermes desde Python.** Lo que pasa por él hoy
(medido 23-11, DGX-1799): el BFF del panel de Skirmshop (`bff/app/clients/hermes.py`), el dashboard
DGX para sesiones y turnos (`despacho_base.py`, `routes_studio_agent.py`) y el transporte de jarvis
(`backend/hermes/transport.py`). Quedan por traer, en orden: `routes_hermes_studio.py` y
`routes_hermes_voice.py` del dashboard, el `client.py` de jarvis y los emisores de webhook.

No traduce eventos: cada consumidor mantiene su vocabulario SSE.

## Instalar

```
hermes-api-client @ https://github.com/pocharlies-org/hermes-api-client/releases/download/v1.0.0/hermes_api_client-1.0.0-py3-none-any.whl#sha256=<hex>
```

## Usar

```python
from hermes_api_client import HermesApi, HermesBusy, HermesError

api = HermesApi("https://hermes.example:8642", clave_del_perfil, profile="mi-perfil")

sesion, creada = await api.ensure_session("u-dani", ["Dani", "u-dani"], source="api_server")
filas = await api.messages("u-dani", limit=60)

try:
    async for evento in api.chat_stream("u-dani", "¿qué tengo mañana?"):
        ...                       # evento.event / evento.data, tal como los manda Hermes
except HermesBusy:
    ...                           # la sesión ya tiene un turno en curso (409)
except HermesError as exc:        # exc.status (0 = transporte), exc.retryable, exc.detail
    ...
```

El turno durable: `run_id = await api.start_run(sid, texto)` y `async for frame in api.run_events(run_id)`
(frames de solo datos, cada uno con su clave `event`).

`base_url` puede llevar ya el prefijo `/p/<perfil>`; entonces no se pasa `profile=`.
`api_key` admite un callable, para quien la lee del entorno en caliente.

### Webhooks entrantes

Hermes entrega firmado con `X-Webhook-Signature-V2 = hex(HMAC-SHA256(secreto, "<ts>.<cuerpo>"))`.
Se verifica con los bytes crudos del cuerpo, nunca con el JSON re-serializado:

```python
from hermes_api_client import sign_v2, verify_v2

cab = sign_v2(secreto, cuerpo)                     # las dos cabeceras, para httpx.post(headers=cab)
ok = verify_v2(secreto, request.headers, await request.body())   # bool; ±300 s de reloj, nunca lanza
```

## Pruebas

```
pip install -e .[dev] && pytest -q && ruff check .
```
