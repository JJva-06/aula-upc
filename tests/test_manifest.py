import copy

from aula.manifest import merge, migrate, resumen


def test_migrate_convierte_adjuntos_bloqueados_en_pendientes():
    v1 = {
        "schema_version": 1,
        "documents": [
            {"source_url": "u1", "attachments": [{"name": "a.pdf", "status": "blocked_by_browser_policy"}]}
        ],
    }

    v2 = migrate(v1)

    assert v2["documents"][0]["attachments"][0]["status"] == "pending"
    assert v2["schema_version"] == 2


def test_migrate_preserva_claves_desconocidas_en_todos_los_niveles():
    v1 = {
        "schema_version": 1,
        "grading": {"parcial": 0.3},
        "recordings": [{"url": "x"}],
        "course": {"name": "C", "campo_mio": 42},
        "documents": [
            {
                "source_url": "u1",
                "nota_personal": "revisar",
                "attachments": [{"name": "a.pdf", "status": "downloaded", "tag": "importante"}],
            }
        ],
    }
    original = copy.deepcopy(v1)

    v2 = migrate(v1)

    assert v2["grading"] == {"parcial": 0.3}
    assert v2["recordings"] == [{"url": "x"}]
    assert v2["course"]["campo_mio"] == 42
    assert v2["documents"][0]["nota_personal"] == "revisar"
    assert v2["documents"][0]["attachments"][0]["tag"] == "importante"
    assert v1 == original, "migrate no debe mutar su entrada"


def test_merge_anade_documentos_nuevos():
    actual = {"schema_version": 2, "documents": [{"source_url": "u1", "title": "Uno", "attachments": []}]}
    descubierto = [{"source_url": "u2", "title": "Dos", "attachments": []}]

    out = merge(actual, descubierto)

    assert [d["source_url"] for d in out["documents"]] == ["u1", "u2"]


def test_merge_anade_adjuntos_nuevos_a_un_documento_existente():
    actual = {
        "schema_version": 2,
        "documents": [{"source_url": "u1", "attachments": [{"name": "a.pdf", "status": "downloaded"}]}],
    }
    descubierto = [
        {"source_url": "u1", "attachments": [{"name": "a.pdf"}, {"name": "b.pdf"}]}
    ]

    out = merge(actual, descubierto)

    adjuntos = out["documents"][0]["attachments"]
    assert [a["name"] for a in adjuntos] == ["a.pdf", "b.pdf"]
    assert adjuntos[1]["status"] == "pending", "el adjunto nuevo entra como pendiente"


def test_merge_no_pisa_ediciones_manuales_ni_estados_ya_descargados():
    actual = {
        "schema_version": 2,
        "documents": [{
            "source_url": "u1",
            "title": "Mi título corregido",
            "week": 7,
            "attachments": [{"name": "a.pdf", "status": "downloaded", "sha256": "abc"}],
        }],
    }
    descubierto = [{
        "source_url": "u1",
        "title": "Título crudo del Aula Virtual",
        "week": None,
        "attachments": [{"name": "a.pdf", "status": "pending"}],
    }]

    out = merge(actual, descubierto)
    doc = out["documents"][0]

    assert doc["title"] == "Mi título corregido"
    assert doc["week"] == 7
    assert doc["attachments"][0]["status"] == "downloaded"
    assert doc["attachments"][0]["sha256"] == "abc"


def test_resumen_cuenta_adjuntos_por_estado():
    data = {"documents": [
        {"attachments": [{"name": "a", "status": "downloaded"}, {"name": "b", "status": "pending"}]},
        {"attachments": [{"name": "c", "status": "pending"}, {"name": "d", "status": "unavailable"}]},
        {"attachments": []},
    ]}

    assert resumen(data) == {"downloaded": 1, "pending": 2, "unavailable": 1}


def test_resumen_de_un_manifiesto_sin_documentos_es_vacio():
    assert resumen({}) == {}


def test_merge_rellena_campos_ausentes_de_un_adjunto_existente():
    """La URL de descarga es información nueva que trae el descubrimiento: debe
    completarse en los adjuntos que ya estaban, o sync no sabría de dónde bajarlos."""
    actual = {"documents": [{"source_url": "u1", "attachments": [{"name": "a.pdf", "status": "pending"}]}]}
    descubierto = [{"source_url": "u1", "attachments": [{"name": "a.pdf", "url": "https://x/bbcswebdav/1", "status": "pending"}]}]

    out = merge(actual, descubierto)

    assert out["documents"][0]["attachments"][0]["url"] == "https://x/bbcswebdav/1"


def test_merge_rellena_pero_nunca_pisa_lo_que_escribio_sync():
    actual = {"documents": [{"source_url": "u1", "attachments": [
        {"name": "a.pdf", "status": "downloaded", "sha256": "abc"}]}]}
    descubierto = [{"source_url": "u1", "attachments": [
        {"name": "a.pdf", "status": "pending", "sha256": "otro"}]}]

    out = merge(actual, descubierto)
    att = out["documents"][0]["attachments"][0]

    assert att["status"] == "downloaded"
    assert att["sha256"] == "abc"


def test_merge_refresca_la_url_aunque_el_adjunto_ya_estuviera():
    """La URL es transporte, no estado: las de bbcswebdav van firmadas y caducan.

    Conservar la vieja deja inútil la redescubierta que hace `sync` antes de bajar,
    y el archivo falla justo cuando hay que rebajarlo.
    """
    actual = {"documents": [{"source_url": "u1", "attachments": [
        {"name": "a.pdf", "status": "downloaded", "url": "https://viejo-caducado"}]}]}
    descubierto = [{"source_url": "u1", "attachments": [
        {"name": "a.pdf", "status": "pending", "url": "https://recien-firmado"}]}]

    out = merge(actual, descubierto)
    att = out["documents"][0]["attachments"][0]

    assert att["url"] == "https://recien-firmado"
    assert att["status"] == "downloaded", "refrescar la URL no revive el estado"


def test_merge_no_borra_la_url_si_el_descubrimiento_no_trae_ninguna():
    """Un item sin URL descargable (un enlace, un adjunto que Ultra dejó de servir)
    no debe dejar al adjunto sin la única URL que se tenía."""
    actual = {"documents": [{"source_url": "u1", "attachments": [
        {"name": "a.pdf", "status": "pending", "url": "https://la-unica"}]}]}
    descubierto = [{"source_url": "u1", "attachments": [{"name": "a.pdf"}]}]

    out = merge(actual, descubierto)

    assert out["documents"][0]["attachments"][0]["url"] == "https://la-unica"


def test_merge_no_duplica_documentos_que_comparten_url_en_el_mismo_lote():
    # Dos hallazgos de la misma página navegable llegan juntos: son un documento.
    descubiertos = [
        {"source_url": "u1", "week": 1, "attachments": [{"name": "a.pdf"}]},
        {"source_url": "u1", "week": 1, "attachments": [{"name": "b.pdf"}]},
    ]

    salida = merge({"documents": []}, descubiertos)

    assert len(salida["documents"]) == 1
    assert [a["name"] for a in salida["documents"][0]["attachments"]] == ["a.pdf", "b.pdf"]
