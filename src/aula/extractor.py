"""Extrae adjuntos y enlaces del campo `body` de un documento de Blackboard Ultra.

Los archivos no están en `/contents/{id}/attachments` (ese endpoint responde 400 en
los cursos que usamos): viven incrustados en el HTML del `body`, como enlaces a
`/bbcswebdav/...` con un atributo `data-bbfile` que lleva el nombre real del archivo.

Función pura sobre un string: sin red, sin navegador.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser

MARCA_ADJUNTO = "/bbcswebdav/"


class _Enlaces(HTMLParser):
    """Recoge cada <a> con su href, su texto visible y su data-bbfile."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.enlaces: list[dict] = []
        self._actual: dict | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag != "a":
            return
        d = dict(attrs)
        if not d.get("href"):
            return
        self._actual = {"href": d["href"], "bbfile": d.get("data-bbfile"), "texto": ""}

    def handle_data(self, data: str) -> None:
        if self._actual is not None:
            self._actual["texto"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._actual is not None:
            self.enlaces.append(self._actual)
            self._actual = None


def _nombre(enlace: dict) -> str:
    """El nombre de archivo real, con el texto visible como respaldo."""
    crudo = enlace.get("bbfile")
    if crudo:
        try:
            if nombre := json.loads(crudo).get("linkName"):
                return nombre
        except (ValueError, AttributeError):
            pass  # data-bbfile malformado: se cae al texto visible
    return enlace["texto"].strip()


def contenido_del_body(body: str | None) -> dict:
    """Separa los adjuntos descargables de los enlaces externos."""
    if not body:
        return {"attachments": [], "external_links": []}

    parser = _Enlaces()
    parser.feed(body)

    adjuntos: list[dict] = []
    externos: list[dict] = []
    vistos: set[str] = set()

    for enlace in parser.enlaces:
        href = enlace["href"]
        if MARCA_ADJUNTO in href:
            if href in vistos:
                continue
            vistos.add(href)
            adjuntos.append({"name": _nombre(enlace), "url": href, "status": "pending"})
        else:
            externos.append({"title": enlace["texto"].strip(), "url": href})

    return {"attachments": adjuntos, "external_links": externos}
