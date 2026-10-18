"""El extractor saca adjuntos y enlaces del `body` HTML de un documento de Ultra."""

from aula.extractor import contenido_del_body

BBCSWEBDAV = "https://aulavirtual.upc.edu.pe/bbcswebdav/pid-72607295-dt-content-rid-1266280375_1/xid-1266280375_1?Kq3cZcYS15=abc"


def test_un_enlace_bbcswebdav_es_un_adjunto():
    body = f'<p><a href="{BBCSWEBDAV}">Sesion1-1.pdf</a></p>'

    out = contenido_del_body(body)

    assert out["attachments"] == [{"name": "Sesion1-1.pdf", "url": BBCSWEBDAV, "status": "pending"}]
    assert out["external_links"] == []


def test_el_nombre_real_sale_de_data_bbfile_no_del_texto():
    """El texto visible suele ser una etiqueta ('Ejemplo UPC beamer'); el nombre de
    archivo verdadero viaja en data-bbfile.linkName."""
    body = (
        f'<a href="{BBCSWEBDAV}" data-bbfile="{{&quot;linkName&quot;:&quot;UPC-beamer(7).rar&quot;,'
        f'&quot;mimeType&quot;:&quot;application/x-rar-compressed&quot;}}">Ejemplo UPC beamer</a>'
    )

    out = contenido_del_body(body)

    assert out["attachments"][0]["name"] == "UPC-beamer(7).rar"


def test_los_enlaces_externos_no_se_confunden_con_adjuntos():
    body = '<a href="https://icml.cc/">Machine Learning</a>'

    out = contenido_del_body(body)

    assert out["attachments"] == []
    assert out["external_links"] == [{"title": "Machine Learning", "url": "https://icml.cc/"}]


def test_un_body_vacio_no_produce_nada():
    for vacio in ("", None):
        assert contenido_del_body(vacio) == {"attachments": [], "external_links": []}


def test_no_repite_un_mismo_adjunto():
    body = f'<a href="{BBCSWEBDAV}">Sesion1-1.pdf</a><a href="{BBCSWEBDAV}">Sesion1-1.pdf</a>'

    assert len(contenido_del_body(body)["attachments"]) == 1
