"""El cliente contra un `httpx.MockTransport` que habla el formato real del api_server."""

from __future__ import annotations

import json

import httpx
import pytest

from hermes_api_client import HermesApi, HermesBusy, HermesConfigError, HermesError

BASE = "http://hermes.example:8642"
KEY = "clave-de-prueba"


def make(handler, **kwargs) -> tuple[HermesApi, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    api = HermesApi(BASE, KEY, client=httpx.AsyncClient(transport=httpx.MockTransport(wrapped)), **kwargs)
    return api, seen


def frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


STREAM = frame("assistant.delta", {"delta": "hola "}) + frame("assistant.delta", {"delta": "mundo"}) + ": keepalive\n\n"


# ── la clave ──
def test_empty_key_fails_closed_at_construction() -> None:
    with pytest.raises(HermesConfigError):
        HermesApi(BASE, "")


async def test_callable_key_checked_per_call() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    api = HermesApi(BASE, lambda: "", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(HermesConfigError):
        await api.list_sessions()
    assert seen == []


# ── las rutas ──
async def test_profile_prefixes_and_headers() -> None:
    api, seen = make(lambda r: httpx.Response(200, json={"data": []}), profile="jarvis")
    assert await api.list_sessions(limit=200) == []
    assert seen[0].url.path == "/p/jarvis/api/sessions"
    assert seen[0].url.params["limit"] == "200"
    assert seen[0].headers["Authorization"] == f"Bearer {KEY}"


async def test_base_url_already_carrying_the_profile() -> None:
    api, seen = make(lambda r: httpx.Response(200, json={"status": "ok"}))
    assert await api.health() is True
    assert seen[0].url.path == "/health"


# ── los errores ──
async def test_http_error_carries_status_code_detail_and_retryable() -> None:
    api, _ = make(lambda r: httpx.Response(
        401, json={"error": {"message": "Invalid API key", "code": "gateway_auth_failed"}}))
    with pytest.raises(HermesError) as info:
        await api.list_sessions()
    assert info.value.status == 401
    assert info.value.code == "gateway_auth_failed"
    assert info.value.detail.startswith('{"error"')
    assert info.value.retryable is False


async def test_transport_failure_is_status_zero_and_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    api, _ = make(handler)
    with pytest.raises(HermesError) as info:
        await api.list_sessions()
    assert info.value.status == 0 and info.value.retryable is True
    assert isinstance(info.value.__cause__, httpx.ConnectError)


async def test_health_is_false_on_failure_and_never_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    api, _ = make(handler)
    assert await api.health() is False


# ── sesiones ──
async def test_get_session_404_is_none() -> None:
    api, _ = make(lambda r: httpx.Response(404, json={"error": {"message": "nope"}}))
    assert await api.get_session("no-existe") is None


async def test_ensure_session_existing_does_not_post() -> None:
    api, seen = make(lambda r: httpx.Response(200, json={"session": {"id": "s-1", "title": "T"}}))
    sesion, creada = await api.ensure_session("s-1", ["T"], source="api_server")
    assert (sesion, creada) == ({"id": "s-1", "title": "T"}, False)
    assert [r.method for r in seen] == ["GET"]


async def test_ensure_session_ladder_skips_a_used_title() -> None:
    usados: set[str] = {"repetido"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={"error": {"message": "no existe"}})
        body = json.loads(request.content)
        if body["title"] in usados:
            return httpx.Response(400, json={"error": {"message": "title already in use"}})
        usados.add(body["title"])
        return httpx.Response(201, json={"session": {**body}})

    api, seen = make(handler)
    sesion, creada = await api.ensure_session("s-1", ["repetido", "libre"], source="api_server")
    assert creada is True
    assert sesion["title"] == "libre"
    assert [r.method for r in seen] == ["GET", "POST", "POST"]
    assert json.loads(seen[-1].content)["source"] == "api_server"


async def test_ensure_session_409_race_rereads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={"error": {"message": "todavía no"}}) if len(seen) == 1 else \
                httpx.Response(200, json={"session": {"id": "s-1", "title": "el de otro"}})
        return httpx.Response(409, json={"error": {"code": "session_exists"}})

    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    api = HermesApi(BASE, KEY, client=httpx.AsyncClient(transport=httpx.MockTransport(wrapped)))
    sesion, creada = await api.ensure_session("s-1", ["T"])
    assert (sesion, creada) == ({"id": "s-1", "title": "el de otro"}, False)


async def test_ensure_session_exhausted_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={"error": {"message": "no existe"}})
        return httpx.Response(400, json={"error": {"message": "title already in use"}})

    api, _ = make(handler)
    with pytest.raises(HermesError) as info:
        await api.ensure_session("s-1", ["a", "b"])
    assert info.value.code == "session_title_rejected"


async def test_create_session_409_is_not_busy() -> None:
    api, _ = make(lambda r: httpx.Response(409, json={"error": {"code": "session_exists"}}))
    with pytest.raises(HermesError) as info:
        await api.create_session("s-1", "T")
    assert not isinstance(info.value, HermesBusy)


# ── el turno por chat/stream ──
async def test_chat_stream_path_headers_body_and_events() -> None:
    api, seen = make(lambda r: httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                             content=STREAM.encode()))
    eventos = [e async for e in api.chat_stream("s-1", "hola", model="fast", session_key="k")]
    req = seen[0]
    assert req.method == "POST"
    assert req.url.path == "/api/sessions/s-1/chat/stream"
    assert req.headers["X-Hermes-Session-Key"] == "k"
    assert req.headers["Accept"] == "text/event-stream"
    assert json.loads(req.content) == {"message": "hola", "model": "fast"}
    assert [e.event for e in eventos] == ["assistant.delta", "assistant.delta"]
    assert eventos[0].data["delta"] == "hola "


async def test_chat_stream_include_tool_results() -> None:
    api, seen = make(lambda r: httpx.Response(200, content=b""))
    _ = [e async for e in api.chat_stream("s-1", "hola", include_tool_results=True)]
    assert json.loads(seen[0].content) == {"message": "hola", "include_tool_results": True}


async def test_chat_stream_busy_is_hermes_busy() -> None:
    api, _ = make(lambda r: httpx.Response(409, text='{"error": {"code": "session_busy"}}'))
    with pytest.raises(HermesBusy) as info:
        _ = [e async for e in api.chat_stream("s-1", "hola")]
    assert info.value.status == 409
    assert "session_busy" in info.value.detail


async def test_chat_stream_transport_failure_keeps_the_cause() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("reset", request=request)

    api, _ = make(handler)
    with pytest.raises(HermesError) as info:
        _ = [e async for e in api.chat_stream("s-1", "hola")]
    assert info.value.status == 0
    assert isinstance(info.value.__cause__, httpx.ReadError)


class _Bytes(httpx.AsyncByteStream):
    """Un stream de verdad (MockTransport con `content=` lee el cuerpo por adelantado)."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self):
        yield self._data


async def test_chat_stream_raw_is_byte_for_byte() -> None:
    crudo = STREAM.encode() + b"\xc3\xa9"  # bytes sueltos: no se tocan
    api, _ = make(lambda r: httpx.Response(200, stream=_Bytes(crudo)))
    trozos = [c async for c in api.chat_stream_raw("s-1", "hola")]
    assert b"".join(trozos) == crudo


# ── el turno por /v1/runs ──
async def test_start_run_returns_the_run_id() -> None:
    api, seen = make(lambda r: httpx.Response(202, json={"run_id": "run_1", "status": "queued"}))
    assert await api.start_run("s-1", "hola") == "run_1"
    assert json.loads(seen[0].content) == {"input": "hola", "session_id": "s-1"}


async def test_start_run_without_run_id() -> None:
    api, _ = make(lambda r: httpx.Response(202, json={"status": "queued"}))
    with pytest.raises(HermesError) as info:
        await api.start_run("s-1", "hola")
    assert info.value.status == 502 and info.value.code == "no_run_id"


async def test_start_run_busy() -> None:
    api, _ = make(lambda r: httpx.Response(409, json={"error": {"code": "session_busy"}}))
    with pytest.raises(HermesBusy):
        await api.start_run("s-1", "hola")


async def test_run_events_are_data_only_frames() -> None:
    stream = (': open\n\ndata: {"event": "message.delta", "delta": "va"}\n\n'
              'data: {"event": "run.completed", "output": "fin"}\n\n')
    api, seen = make(lambda r: httpx.Response(200, content=stream.encode()))
    frames = [f async for f in api.run_events("run_1")]
    assert seen[0].url.path == "/v1/runs/run_1/events"
    assert frames == [{"event": "message.delta", "delta": "va"}, {"event": "run.completed", "output": "fin"}]


async def test_stop_and_approve_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stop"):
            return httpx.Response(200, json={"status": "stopping"})
        return httpx.Response(200, json={"status": "approved"})

    api, seen = make(handler)
    await api.stop_run("run_1", timeout=5.0)
    assert await api.approve_run("run_1", "accept", request_id="r-2") == {"status": "approved"}
    assert json.loads(seen[-1].content) == {"choice": "accept", "request_id": "r-2"}


async def test_update_session_and_answer_clarify() -> None:
    api, seen = make(lambda r: httpx.Response(200, json={"session": {"id": "s-1", "archived": True}}))
    assert await api.update_session("s-1", archived=True) == {"id": "s-1", "archived": True}
    assert seen[0].method == "PATCH"
    await api.answer_clarify("s-1", "c-9", "sí")
    assert seen[-1].url.path == "/api/sessions/s-1/clarify/c-9"
    assert json.loads(seen[-1].content) == {"response": "sí"}


# ── timeouts ──
async def test_stream_uses_its_own_timeout_and_json_keeps_the_default() -> None:
    """El stream de un turno (120 s de silencio) no puede heredar el corto de las llamadas JSON."""

    visto: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visto.append((request.url.path, dict(request.extensions["timeout"])))
        if request.url.path.endswith("/events"):
            return httpx.Response(200, stream=_Bytes(b'data: {"event": "run.completed"}\n\n'))
        return httpx.Response(200, json={"session": {"id": "s-1"}})

    api, _ = make(handler, timeout=5.0, stream_timeout=httpx.Timeout(120.0, connect=5.0))
    assert [f async for f in api.run_events("run_1")] == [{"event": "run.completed"}]
    await api.get_session("s-1")
    assert visto == [
        ("/v1/runs/run_1/events", {"connect": 5.0, "read": 120.0, "write": 120.0, "pool": 120.0}),
        ("/api/sessions/s-1", {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}),
    ]
