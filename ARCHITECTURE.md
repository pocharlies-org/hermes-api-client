# Arquitectura

Una librería, tres módulos, una dependencia (`httpx`). Nace en DGX-1301 (nota del architect,
adjunta en el ticket): cuatro sistemas hablaban con el api_server de Hermes cada uno con su
cliente, y lo que ya había divergido — la escalera de títulos de sesión, el 409
`session_busy`, los estados reintentables y el parser SSE — estaba reescrito cuatro veces.

## Qué es y qué no es

Es el único sitio que sabe **cómo se llama al api_server de Hermes desde Python**: las rutas,
el encuadre SSE, la escalera de títulos y la clasificación de errores.

**No traduce eventos.** De aquí salen las filas crudas de la API y los `SseEvent` tal como los
manda Hermes. El vocabulario que ve cada usuario (los eventos `meta|text|tool|done` del panel,
los `choices` del dashboard, el reenvío en crudo del relé) se queda en el consumidor, junto con
sus guardas: el HMAC de la sesión por usuario, el contexto de la página, el un-turno-por-usuario,
los reintentos y las colas por turno.

## Los dos modos de turno

Hermes admite dos, y los consumidores no los usan igual — la librería expone los dos, no los
unifica:

- `chat/stream` (`chat_stream`, `chat_stream_raw`): SSE **con nombre** de evento. Es el del
  dashboard DGX, el relé de AlphaLink y el cerebro rápido de jarvis. Es el único con `clarify`
  (`answer_clarify`).
- `/v1/runs` (`start_run` + `run_events`, `stop_run`, `approve_run`): SSE **de solo datos**,
  durable (el turno sigue vivo si el relé cae) y el único con aprobaciones. Es el del panel de
  Skirmshop, el Despacho del CTO y el cerebro «full» de jarvis.

Cambiar de modo en un consumidor cambiaría su comportamiento; por eso la elección sigue siendo suya.

## Errores

`HermesError(status, code, detail)` con `.retryable` (`0, 429, 502, 503, 504`); `status=0` es un
fallo de transporte y `__cause__` guarda la excepción de httpx, que es el nombre que los
consumidores enseñan. `HermesBusy` (409 `session_busy`) y `HermesConfigError` (sin clave: falla
antes de tocar la red). La traducción a HTTP propio (`HTTPException`, `UpstreamError`, frames
`event: error`) es del consumidor.

## Versionado y consumo

Los consumidores fijan la versión y el hash en su `requirements.txt` / `pyproject.toml`:

```
hermes-api-client @ https://github.com/.../releases/download/v1.0.0/hermes_api_client-1.0.0-py3-none-any.whl#sha256=<hex>
```

Sin git ni credenciales en las imágenes. Un cambio de API de Hermes = un PR aquí y cuatro pins.

- **Aditivo** (un parámetro con defecto, un método nuevo que ya usa un consumidor real): sube el
  menor. Todo lo que entra tiene que estar usado por alguien: no hay superficie «para cuando haga
  falta».
- **Rompedor** (renombrar, quitar, cambiar un defecto): `.v2` y el viejo `deprecated`, como el
  resto de la casa.

## CI

`tests.yml` y `release.yml` en `arc-k8s`. El release (tag `v*`) construye el wheel y lo sube a la
GitHub Release con el `GITHUB_TOKEN` del repo: sin secretos.

## Límites del repo (veredicto de security, DGX-1301)

Público: la forma de las rutas ya es pública en el upstream. A cambio: nunca una clave real
(tampoco en fixtures), nada de IPs de tailnet/LAN ni del hostname público del borde (aquí se usa
`https://hermes.example`), y ningún contenido real de conversación en las pruebas.
