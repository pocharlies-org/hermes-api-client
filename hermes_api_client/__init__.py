"""Cliente Python del api_server de Hermes: sesiones, historial, los dos modos de turno y los webhooks entrantes."""

from .client import HermesApi
from .errors import RETRYABLE_STATUSES, HermesBusy, HermesConfigError, HermesError
from .sse import SseEvent, SseParser, aiter_data_frames, aiter_sse, iter_sse
from .webhooks import SIGNATURE_HEADER, SKEW_S, TIMESTAMP_HEADER, firma, sign_v2, verify_v2

__all__ = [
    "HermesApi",
    "HermesBusy",
    "HermesConfigError",
    "HermesError",
    "RETRYABLE_STATUSES",
    "SIGNATURE_HEADER",
    "SKEW_S",
    "SseEvent",
    "SseParser",
    "TIMESTAMP_HEADER",
    "aiter_data_frames",
    "aiter_sse",
    "firma",
    "iter_sse",
    "sign_v2",
    "verify_v2",
]

__version__ = "1.2.0"
