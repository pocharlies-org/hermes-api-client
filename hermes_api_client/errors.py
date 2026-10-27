"""Los errores del cliente: lo que cada consumidor traduce a su propio lenguaje HTTP."""

from __future__ import annotations

#: Estados por los que merece la pena reintentar contra la misma sesión: el pod se
#: reinicia, está drenando o ha pasado su tope de turnos.
RETRYABLE_STATUSES = frozenset({0, 429, 502, 503, 504})


class HermesError(RuntimeError):
    """Fallo al hablar con el api_server de Hermes.

    ``status`` es el HTTP de la respuesta, o ``0`` en un fallo de transporte (DNS,
    conexión, timeout); en ese caso ``__cause__`` es la excepción de httpx, que es el
    nombre que los consumidores enseñan al usuario. ``detail`` es el cuerpo recibido,
    recortado a 300 caracteres: lo que dgx y el relé de AlphaLink reenvían tal cual.
    """

    def __init__(self, message: str, *, status: int = 0, code: str | None = None, detail: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.detail = detail

    @property
    def retryable(self) -> bool:
        return self.status in RETRYABLE_STATUSES


class HermesBusy(HermesError):
    """La sesión ya tiene un turno en curso (Hermes responde 409 ``session_busy``)."""


class HermesConfigError(HermesError):
    """Falta la credencial: se lanza antes de tocar la red, para que un despliegue mal
    configurado no salga ahí fuera sin autenticar."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status=0)
