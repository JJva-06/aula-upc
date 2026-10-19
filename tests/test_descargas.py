import pytest

from aula import descargas
from aula.descargas import (
    asignar_destinos,
    destino_local,
    nombre_portable,
    ruta_portable,
)


def test_destino_local_no_deja_escapar_de_la_carpeta():
    assert destino_local(3, "..\\..\\x.pdf") == "materials/week-03/x.pdf"
    assert destino_local(None, "a/b/c.pdf") == "materials/extra/c.pdf"


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        ("Sesión 3: Intro.pdf", "Sesión 3_ Intro.pdf"),
        ('¿Qué es "esto"?.pptx', '¿Qué es _esto__.pptx'),
        ("a<b>c|d*e.txt", "a_b_c_d_e.txt"),
        ("final. ", "final"),
        ("CON", "_CON"),
        ("nul.pdf", "_nul.pdf"),
        ("console.pdf", "console.pdf"),
        ("", "_"),
    ],
)
def test_nombre_portable_quita_lo_que_windows_rechaza(nombre, esperado):
    assert nombre_portable(nombre) == esperado


def test_destino_local_es_valido_en_windows():
    assert destino_local(1, "Lab 1: Hilos?.zip") == "materials/week-01/Lab 1_ Hilos_.zip"


def test_ruta_portable():
    assert ruta_portable("materials/week-01/a.pdf")
    assert not ruta_portable("materials/week-01/a: b.pdf")


def _manifiesto(local_path):
    return {"documents": [{"week": 2, "title": "T", "attachments": [{"name": "a: b.pdf", "local_path": local_path}]}]}


def test_en_windows_reasigna_una_ruta_registrada_que_alli_no_puede_existir(monkeypatch):
    monkeypatch.setattr(descargas, "EN_WINDOWS", True)

    out = asignar_destinos(_manifiesto("materials/week-02/a: b.pdf"))

    assert out["documents"][0]["attachments"][0]["local_path"] == "materials/week-02/a_ b.pdf"


def test_fuera_de_windows_respeta_la_ruta_registrada(monkeypatch):
    monkeypatch.setattr(descargas, "EN_WINDOWS", False)

    out = asignar_destinos(_manifiesto("materials/week-02/a: b.pdf"))

    assert out["documents"][0]["attachments"][0]["local_path"] == "materials/week-02/a: b.pdf"
