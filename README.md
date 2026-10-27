# hermes-api-client

Cliente Python (httpx) del [api_server de Hermes](https://github.com/NousResearch/hermes-agent):
asegurar la sesión, leer el historial y lanzar un turno — por `chat/stream` o por `/v1/runs`.

Lo usan el dashboard DGX, jarvis, el relé de AlphaLink y el panel de Skirmshop. No traduce
eventos: cada consumidor mantiene su vocabulario SSE.

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

## Pruebas

```
pip install -e .[dev] && pytest -q && ruff check .
```
