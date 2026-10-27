"""El cliente del api_server de Hermes.

Un solo sitio para lo que los cuatro consumidores hacían cada uno por su cuenta: asegurar
la sesión (Hermes exige títulos únicos: la escalera de candidatos la pasa el consumidor),
el historial, los dos modos de turno — ``chat/stream`` y ``/v1/runs`` — y la clasificación
de errores (el 409 ``session_busy`` y los estados reintentables).

No traduce eventos: de aquí salen las filas crudas de la API y los ``SseEvent`` tal como
los manda Hermes. Quien decide qué ve su usuario sigue siendo el consumidor.

Las rutas van bajo ``{base_url}/p/<perfil>`` cuando se pasa ``profile=``; si la URL base ya
lleva el prefijo (así lo pasan quienes lo leen de una variable de entorno), no se pone.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx

from .errors import HermesBusy, HermesConfigError, HermesError
from .sse import SseEvent, aiter_data_frames, aiter_sse

ApiKey = str | Callable[[], str]


class HermesApi:
    """Cliente asincrónico de un perfil de Hermes (o del perfil raíz si ``profile`` vacío).

    ``api_key`` es la clave o un callable que la devuelve: con un ``str`` se comprueba al
    construir (un despliegue sin clave falla ahí, sin tocar la red); con un callable se
    comprueba en cada llamada, que es lo que necesita quien la lee del entorno en caliente.
    ``client`` inyecta el ``httpx.AsyncClient`` (pruebas con ``MockTransport``).
    """

    def __init__(
        self,
        base_url: str,
        api_key: ApiKey,
        *,
        profile: str = "",
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout | float = 20.0,
        stream_timeout: httpx.Timeout | float | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._profile = profile.strip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._stream_timeout = stream_timeout if stream_timeout is not None else timeout
        self._http = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        if isinstance(api_key, str):
            self._headers()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def __aenter__(self) -> HermesApi:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # ── transporte ──
    def _headers(self) -> dict[str, str]:
        key = self._api_key() if callable(self._api_key) else self._api_key
        if not (key or "").strip():
            raise HermesConfigError("falta la clave del api_server de Hermes")
        return {"Authorization": f"Bearer {key.strip()}"}

    def _url(self, path: str) -> str:
        prefijo = f"/p/{self._profile}" if self._profile else ""
        return f"{self._base}{prefijo}{path}"

    async def _request(self, method: str, path: str, *, timeout: Any = None, **kwargs: Any) -> httpx.Response:
        try:
            resp = await self._http.request(method, self._url(path), headers=self._headers(),
                                            timeout=timeout if timeout is not None else self._timeout, **kwargs)
        except httpx.TransportError as exc:
            raise HermesError(f"hermes unreachable: {exc!r}", status=0) from exc
        if resp.status_code >= 400:
            raise _error_from_response(resp.status_code, resp.content)
        return resp

    async def _json(self, method: str, path: str, *, timeout: Any = None, **kwargs: Any) -> Any:
        return (await self._request(method, path, timeout=timeout, **kwargs)).json() or {}

    @asynccontextmanager
    async def _stream(self, method: str, path: str, body: dict[str, Any] | None,
                      extra_headers: dict[str, str] | None = None):
        """Abre el SSE con su timeout; el error ya sale clasificado (incluido el busy)."""

        headers = {"Accept": "text/event-stream", **self._headers(), **(extra_headers or {})}
        try:
            # El timeout va en la petición (httpx lo lee de ahí, `send` no lo acepta): sin él
            # el stream heredaría el defecto del cliente, que es el corto de las llamadas JSON.
            request = self._http.build_request(method, self._url(path), headers=headers, json=body,
                                               timeout=self._stream_timeout)
            resp = await self._http.send(request, stream=True)
        except httpx.TransportError as exc:
            raise HermesError(f"hermes stream failed: {exc!r}", status=0) from exc
        try:
            if resp.status_code >= 400:
                raise _error_from_response(resp.status_code, await resp.aread())
            yield resp
        finally:
            await resp.aclose()

    # ── health ──
    async def health(self) -> bool:
        """``GET /health``: sin prefijo de perfil y sin exigir clave (es la sonda de siempre)."""

        try:
            resp = await self._http.get(f"{self._base}/health", timeout=5.0)
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            return False
        try:
            return (resp.json() or {}).get("status") == "ok"
        except ValueError:
            return False

    # ── sesiones ──
    async def get_session(self, sid: str) -> dict[str, Any] | None:
        """La sesión, o ``None`` si no existe (404)."""

        try:
            data = await self._json("GET", f"/api/sessions/{sid}")
        except HermesError as exc:
            if exc.status == 404:
                return None
            raise
        return _session_of(data)

    async def create_session(self, sid: str, title: str | None = None, *, source: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"id": sid}
        if title:
            body["title"] = title
        if source:
            body["source"] = source
        return _session_of(await self._json("POST", "/api/sessions", json=body))

    async def ensure_session(self, sid: str, titles: Sequence[str], *,
                             source: str | None = None) -> tuple[dict[str, Any], bool]:
        """La sesión ``sid``, creada si no existe. Devuelve ``(sesión, creada)``.

        Hermes exige títulos únicos: se prueban los candidatos en orden y un 400 que hable
        del título pasa al siguiente. Un 409 es que ya existe (la creó otro a la vez): se
        releé; si no se puede leer se devuelve el id, que era lo que se pedía.
        """

        existing = await self.get_session(sid)
        if existing:
            return existing, False
        for titulo in titles:
            try:
                created = await self.create_session(sid, titulo, source=source)
            except HermesError as exc:
                if exc.status == 409:
                    return (await self.get_session(sid)) or {"id": sid, "title": titulo}, False
                if exc.status == 400 and "title" in exc.detail.lower():
                    continue
                raise
            return {**created, "id": sid}, True
        raise HermesError("hermes no aceptó ningún título de sesión", status=400, code="session_title_rejected")

    async def list_sessions(self, *, limit: int = 200, offset: int | None = None,
                            source: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if offset is not None:
            params["offset"] = offset
        if source:
            params["source"] = source
        return _rows(await self._json("GET", "/api/sessions", params=params))

    async def messages(self, sid: str, *, limit: int = 50, offset: int | None = None,
                       order: str = "latest") -> list[dict[str, Any]]:
        """Las filas crudas del historial. Un 404 lanza: quién decide si es ``[]`` o un 404
        para su usuario es el consumidor."""

        params: dict[str, Any] = {"limit": limit, "order": order}
        if offset is not None:
            params["offset"] = offset
        return _rows(await self._json("GET", f"/api/sessions/{sid}/messages", params=params))

    async def update_session(self, sid: str, **campos: Any) -> dict[str, Any]:
        return _session_of(await self._json("PATCH", f"/api/sessions/{sid}", json=campos))

    # ── el turno por chat/stream (SSE con nombre) ──
    def chat_stream(self, sid: str, message: Any, *, model: str | None = None,
                    session_key: str | None = None, include_tool_results: bool = False) -> AsyncIterator[SseEvent]:
        async def generar() -> AsyncIterator[SseEvent]:
            headers = {"X-Hermes-Session-Key": session_key} if session_key else None
            async with self._stream("POST", f"/api/sessions/{sid}/chat/stream",
                                    _chat_body(message, model, include_tool_results), headers) as resp:
                async for evento in aiter_sse(_lineas(resp)):
                    yield evento

        return generar()

    def chat_stream_raw(self, sid: str, message: Any, *, model: str | None = None,
                        include_tool_results: bool = False) -> AsyncIterator[bytes]:
        """Los bytes del stream tal cual, para quien los reenvía a su navegador sin tocarlos."""

        async def generar() -> AsyncIterator[bytes]:
            async with self._stream("POST", f"/api/sessions/{sid}/chat/stream",
                                    _chat_body(message, model, include_tool_results)) as resp:
                try:
                    async for trozo in resp.aiter_raw():
                        yield trozo
                except httpx.TransportError as exc:
                    raise HermesError(f"hermes stream failed: {exc!r}", status=0) from exc

        return generar()

    async def answer_clarify(self, sid: str, clarify_id: str, response: str) -> dict[str, Any]:
        return await self._json("POST", f"/api/sessions/{sid}/clarify/{clarify_id}", json={"response": response})

    # ── el turno durable por /v1/runs (SSE de solo datos) ──
    async def start_run(self, sid: str, input: Any, *, model: str | None = None) -> str:
        body: dict[str, Any] = {"input": input, "session_id": sid}
        if model:
            body["model"] = model
        data = await self._json("POST", "/v1/runs", json=body)
        run_id = data.get("run_id") if isinstance(data, dict) else None
        if not run_id:
            raise HermesError("hermes /v1/runs respondió sin run_id", status=502, code="no_run_id")
        return str(run_id)

    def run_events(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        async def generar() -> AsyncIterator[dict[str, Any]]:
            async with self._stream("GET", f"/v1/runs/{run_id}/events", None) as resp:
                async for frame in aiter_data_frames(_lineas(resp)):
                    yield frame

        return generar()

    async def stop_run(self, run_id: str, *, timeout: Any = None) -> None:
        await self._request("POST", f"/v1/runs/{run_id}/stop", json={}, timeout=timeout)

    async def approve_run(self, run_id: str, choice: str, *, request_id: str | None = None,
                          timeout: Any = None) -> dict[str, Any]:
        body: dict[str, Any] = {"choice": choice}
        if request_id:
            body["request_id"] = request_id
        return (await self._request("POST", f"/v1/runs/{run_id}/approval", json=body, timeout=timeout)).json() or {}


async def _lineas(resp: httpx.Response) -> AsyncIterator[str]:
    try:
        async for linea in resp.aiter_lines():
            yield linea
    except httpx.TransportError as exc:
        raise HermesError(f"hermes stream failed: {exc!r}", status=0) from exc


def _chat_body(message: Any, model: str | None, include_tool_results: bool) -> dict[str, Any]:
    body: dict[str, Any] = {"message": message}
    if model:
        body["model"] = model
    if include_tool_results:
        body["include_tool_results"] = True
    return body


def _session_of(data: Any) -> dict[str, Any]:
    return (data or {}).get("session") or {}


def _rows(data: Any) -> list[dict[str, Any]]:
    filas = (data or {}).get("data")
    return filas if isinstance(filas, list) else []


def _error_from_response(status: int, body: bytes | str) -> HermesError:
    texto = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    mensaje, code = texto[:300] or f"HTTP {status}", None
    try:
        err = (json.loads(texto) or {}).get("error")
        if isinstance(err, dict):
            mensaje = str(err.get("message") or mensaje)
            code = err.get("code")
        elif isinstance(err, str):
            mensaje = err
    except (ValueError, AttributeError):
        pass
    mensaje = f"hermes HTTP {status}: {mensaje}"
    if status == 409 and (code == "session_busy" or "session_busy" in texto):
        return HermesBusy(mensaje, status=status, code=code, detail=texto[:300])
    return HermesError(mensaje, status=status, code=code, detail=texto[:300])
