"""El encuadre SSE de Hermes (subconjunto WHATWG): ``event:`` + ``data:``, línea en blanco
despacha, ``:`` es comentario (los keepalive). Un ``data`` que no es JSON sale como
``{"raw": <texto>}`` para que ningún consumidor pierda el turno por un frame raro."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SseEvent:
    """Un evento despachado (sin nombre ``event:`` el nombre es ``message``)."""

    event: str
    data: dict[str, Any] = field(default_factory=dict)


class SseParser:
    """Máquina de estados del encuadre: ``feed(linea)`` devuelve el evento cuando la línea
    en blanco lo cierra (``None`` en cualquier otro caso)."""

    def __init__(self) -> None:
        self._event = ""
        self._data: list[str] = []

    def feed(self, line: str) -> SseEvent | None:
        line = line.rstrip("\r")
        if not line:
            if not self._data and not self._event:
                return None
            name = self._event or "message"
            raw = "\n".join(self._data)
            self._event, self._data = "", []
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                data = {"raw": raw}
            if not isinstance(data, dict):
                data = {"value": data}
            return SseEvent(event=name, data=data)
        if line.startswith(":"):
            return None  # comentario / keepalive
        field_name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field_name == "event":
            self._event = value
        elif field_name == "data":
            self._data.append(value)
        return None


def iter_sse(lines: Iterable[str]) -> Iterator[SseEvent]:
    """Versión sincrónica (para pruebas y para quien ya tiene las líneas en memoria)."""

    parser = SseParser()
    for line in lines:
        event = parser.feed(line)
        if event is not None:
            yield event
    tail = parser.feed("")
    if tail is not None:
        yield tail


async def aiter_sse(lines: AsyncIterator[str]) -> AsyncIterator[SseEvent]:
    """Versión asincrónica, con el mismo cierre al final del stream."""

    parser = SseParser()
    async for line in lines:
        event = parser.feed(line)
        if event is not None:
            yield event
    tail = parser.feed("")
    if tail is not None:
        yield tail


async def aiter_data_frames(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """Los frames de ``GET /v1/runs/{id}/events``: SOLO datos (``data: {"event": …}``); los
    comentarios (``: open``, ``: keepalive``, ``: stream closed``) no llevan evento y se
    descartan, igual que los JSON sin la clave ``event``."""

    data: list[str] = []
    async for line in lines:
        if line == "":
            for frame in _flush(data):
                yield frame
            data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    for frame in _flush(data):
        yield frame


def _flush(datos: list[str]) -> list[dict[str, Any]]:
    if not datos:
        return []
    try:
        payload = json.loads("\n".join(datos))
    except ValueError:
        return []
    return [payload] if isinstance(payload, dict) and payload.get("event") else []
