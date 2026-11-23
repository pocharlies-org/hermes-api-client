"""Los webhooks **entrantes** de Hermes: la firma V2, en los dos sentidos.

Hermes entrega en `POST /p/<perfil>/webhooks/<ruta>` con dos cabeceras, y los sistemas que le
devuelven eventos por esa misma ruta tienen que firmar igual. El esquema vive aquí y en ningún
otro sitio:

    X-Webhook-Signature-V2 = hex(HMAC-SHA256(secreto, "<ts>.<cuerpo>"))

con `X-Webhook-Timestamp` en segundos epoch y `<cuerpo>` los bytes **crudos** que se enviaron (no
el JSON re-serializado: quien verifica vuelve a calcular sobre lo que recibió).

No hay superficie «para cuando haga falta»: `sign_v2` la usan los emisores (el notificador del
cine, el disparador de épicas, las secretarias) y `verify_v2` la usa el dashboard para sus
despachos.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping

#: Cabeceras de la firma. `sign_v2` las devuelve y `verify_v2` las lee.
TIMESTAMP_HEADER = "X-Webhook-Timestamp"
SIGNATURE_HEADER = "X-Webhook-Signature-V2"

#: Tolerancia de reloj entre quien firma y quien verifica. Es la que usaba el dashboard: más que
#: esto ya no es un reloj desviado, es un replay, y se rechaza.
SKEW_S = 300


def _bytes(secreto: bytes | str) -> bytes:
    """El secreto llega como `str` de un env y como `bytes` de un Secret montado: los dos valen."""
    return secreto.encode() if isinstance(secreto, str) else secreto


def _firma(secreto: bytes | str, cuerpo: bytes, ts: int) -> str:
    """El hex de la firma V2 sobre `<ts>.<cuerpo>`. `ts` es explícito para que un test lo fije."""
    return hmac.new(_bytes(secreto), str(ts).encode() + b"." + cuerpo, hashlib.sha256).hexdigest()


def sign_v2(secreto: bytes | str, cuerpo: bytes, ts: int | None = None) -> dict[str, str]:
    """Las dos cabeceras para entregar `cuerpo`. Se usan tal cual en `httpx.post(headers=...)`.

    Sin secreto no se firma: devolver las cabeceras vacías sería entregar en claro creyendo que
    va firmado.
    """
    if not secreto:
        raise ValueError("falta el secreto del webhook: sin él no se firma")
    instante = int(ts) if ts is not None else int(time.time())
    return {TIMESTAMP_HEADER: str(instante), SIGNATURE_HEADER: _firma(secreto, cuerpo, instante)}


def verify_v2(
    secreto: bytes | str,
    cabeceras: Mapping[str, str],
    cuerpo: bytes,
    now: float | None = None,
    skew: int = SKEW_S,
) -> bool:
    """`True` solo si la firma es la nuestra y el sello está dentro del skew. Nunca lanza.

    Falla cerrado en todo lo demás: sin secreto, sin cabeceras, con un `ts` que no es un entero o
    con un reloj fuera de `skew`.
    """
    if not secreto:
        return False
    cab = {k.lower(): v for k, v in cabeceras.items()}  # httpx y Starlette son insensibles; un dict normal, no
    ts = (cab.get(TIMESTAMP_HEADER.lower()) or "").strip()
    firma_recibida = (cab.get(SIGNATURE_HEADER.lower()) or "").strip().lower()
    if not ts or not firma_recibida:
        return False
    try:
        instante = int(ts)
    except ValueError:
        return False
    if abs((time.time() if now is None else now) - instante) > skew:
        return False
    return hmac.compare_digest(_firma(secreto, cuerpo, instante), firma_recibida)
