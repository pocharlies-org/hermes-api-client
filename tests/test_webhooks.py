"""La firma V2 de los webhooks: lo que verifica el dashboard y firman los emisores."""

import hashlib
import hmac
import time

import pytest

from hermes_api_client import SIGNATURE_HEADER, SKEW_S, TIMESTAMP_HEADER, firma, sign_v2, verify_v2

SECRETO = b"secreto-del-webhook"
CUERPO = b'{"key":"SC-1","evento":"abierta"}'


def _ahora() -> int:
    return int(time.time())


def test_round_trip_lo_que_se_firma_se_verifica():
    cab = sign_v2(SECRETO, CUERPO)
    assert set(cab) == {TIMESTAMP_HEADER, SIGNATURE_HEADER}
    assert verify_v2(SECRETO, cab, CUERPO) is True


def test_el_secreto_vale_como_str_y_como_bytes():
    # Un env llega como `str`; un Secret montado, como `bytes`. La firma no puede depender de eso.
    ts = _ahora()
    assert firma("secreto", CUERPO, ts) == firma(b"secreto", CUERPO, ts)


def test_la_firma_es_el_esquema_de_hermes_no_el_nuestro():
    # Calculada aquí con hmac en crudo (sin pasar por el módulo): si un día cambia el encuadre,
    # este test se pone rojo aunque `verify_v2` siga siendo consistente consigo mismo.
    ts, cuerpo = 1_700_000_000, b'{"a":1}'
    esperado = hmac.new(b"s", b"1700000000." + cuerpo, hashlib.sha256).hexdigest()
    assert firma(b"s", cuerpo, ts) == esperado


def test_firma_forjada_no_vale():
    cab = dict(sign_v2(SECRETO, CUERPO))
    cab[SIGNATURE_HEADER] = "0" * 64
    assert verify_v2(SECRETO, cab, CUERPO) is False


def test_secreto_distinto_no_vale():
    cab = sign_v2(SECRETO, CUERPO)
    assert verify_v2(b"otro-secreto", cab, CUERPO) is False


def test_cuerpo_alterado_no_vale():
    # La propiedad que importa: se verifica sobre los bytes crudos recibidos.
    cab = sign_v2(SECRETO, CUERPO)
    assert verify_v2(SECRETO, cab, CUERPO + b" ") is False


def test_sin_cabeceras_o_con_ts_que_no_es_entero():
    assert verify_v2(SECRETO, {}, CUERPO) is False
    assert verify_v2(SECRETO, {TIMESTAMP_HEADER: str(_ahora())}, CUERPO) is False
    assert verify_v2(SECRETO, {SIGNATURE_HEADER: "a" * 64}, CUERPO) is False
    assert verify_v2(SECRETO, {TIMESTAMP_HEADER: "ayer", SIGNATURE_HEADER: "a" * 64}, CUERPO) is False


def test_sin_secreto_falla_cerrado():
    cab = sign_v2(SECRETO, CUERPO)
    assert verify_v2(b"", cab, CUERPO) is False
    with pytest.raises(ValueError):
        sign_v2("", CUERPO)


def test_cabeceras_en_minusculas():
    # Un dict normal de un test o de un proxy que baja las cabeceras: no debe colarse por casing.
    cab = {k.lower(): v for k, v in sign_v2(SECRETO, CUERPO).items()}
    assert verify_v2(SECRETO, cab, CUERPO) is True


def test_el_skew_se_mide_con_un_reloj_fijo():
    # `now` se fija a propósito: con el reloj real el borde exacto del skew es una lotería.
    ts = 1_700_000_000
    cab = sign_v2(SECRETO, CUERPO, ts=ts)
    assert verify_v2(SECRETO, cab, CUERPO, now=ts + SKEW_S) is True
    assert verify_v2(SECRETO, cab, CUERPO, now=ts + SKEW_S + 1) is False
    assert verify_v2(SECRETO, cab, CUERPO, now=ts - SKEW_S - 1) is False
