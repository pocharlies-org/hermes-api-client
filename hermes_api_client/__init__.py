"""Cliente Python del api_server de Hermes: sesiones, historial y los dos modos de turno."""

from .client import HermesApi
from .errors import RETRYABLE_STATUSES, HermesBusy, HermesConfigError, HermesError
from .sse import SseEvent, SseParser, aiter_data_frames, aiter_sse, iter_sse

__all__ = [
    "HermesApi",
    "HermesBusy",
    "HermesConfigError",
    "HermesError",
    "RETRYABLE_STATUSES",
    "SseEvent",
    "SseParser",
    "aiter_data_frames",
    "aiter_sse",
    "iter_sse",
]

__version__ = "1.0.0"
