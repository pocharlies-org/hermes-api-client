"""El encuadre SSE: con nombre, de solo datos, comentarios, `data` multilínea y JSON roto."""

from __future__ import annotations

import asyncio

from hermes_api_client.sse import SseEvent, aiter_data_frames, aiter_sse, iter_sse


def test_iter_sse_named_comments_and_multiline() -> None:
    lines = [": keepalive", "", "event: a", 'data: {"x": 1}', "", "data: line1", "data: line2", ""]
    assert list(iter_sse(lines)) == [
        SseEvent(event="a", data={"x": 1}),
        SseEvent(event="message", data={"raw": "line1\nline2"}),
    ]


def test_iter_sse_flushes_the_last_frame_without_blank_line() -> None:
    assert list(iter_sse(['data: {"event": "done"}'])) == [SseEvent(event="message", data={"event": "done"})]


def test_iter_sse_non_dict_json_wraps() -> None:
    assert list(iter_sse(['data: [1, 2]', ""])) == [SseEvent(event="message", data={"value": [1, 2]})]


async def _lines(*chunks: str):
    for chunk in chunks:
        yield chunk


def test_aiter_sse_matches_the_sync_one() -> None:
    got = asyncio.run(_collect(aiter_sse(_lines('event: t', 'data: {"a": 1}', ""))))
    assert got == [SseEvent(event="t", data={"a": 1})]


def test_aiter_data_frames_keeps_only_json_with_event() -> None:
    stream = ": open\n\n" \
        'data: {"event": "message.delta", "delta": "hola"}\n\n' \
        ": keepalive\n\n" \
        "data: no es json\n\n" \
        'data: {"sin_event": 1}\n\n' \
        'data: {"event": "run.completed", "output": "chao"}'

    async def correr() -> list[dict]:
        lineas = _lines(*stream.split("\n"))
        return [f async for f in aiter_data_frames(lineas)]

    assert asyncio.run(correr()) == [
        {"event": "message.delta", "delta": "hola"},
        {"event": "run.completed", "output": "chao"},
    ]


async def _collect(agen) -> list:
    return [e async for e in agen]
