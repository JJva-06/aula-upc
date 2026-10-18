"""Recorrido del árbol de contenidos de un curso de Blackboard Ultra.

`recorrer` recibe una función `get(path) -> dict | None` en vez de hablar con la red
directamente, para poder probarse contra volcados reales sin navegador.
"""

from __future__ import annotations

import re

from . import cursos as _cursos
from .extractor import contenido_del_body

BASE_API = "https://aulavirtual.upc.edu.pe"
BASE_UI = "https://aulavirtual.upc.edu.pe/ultra/courses"
HANDLER_DOCUMENTO = "resource/x-bb-document"
# Tercera forma de organizar un curso: el archivo se sube suelto a la carpeta, sin
# documento que lo envuelva. Ahí sí responde /contents/{id}/attachments (para
# x-bb-document ese endpoint da 400, ver extractor.py).
HANDLER_ARCHIVO = "resource/x-bb-file"

# Ultra titula así los cuerpos de documento generados por su editor: es un marcador
# interno, no un título. Cursos enteros vienen con todos sus documentos llamados así.
TITULOS_PLACEHOLDER = {"ultraDocumentBody", ""}

_UNIDAD = re.compile(r"^\s*unidad\s+(\d+)", re.IGNORECASE)
_SEMANA = re.compile(r"^\s*semana\s+(\d+)", re.IGNORECASE)


def _numero(patron: re.Pattern, titulo: str) -> int | None:
    m = patron.match(titulo or "")
    return int(m.group(1)) if m else None


def numero_de_unidad(titulo: str) -> int | None:
    """5 para 'Unidad 5: Optimización en Sistemas Multi-agente'."""
    return _numero(_UNIDAD, titulo)


def numero_de_semana(titulo: str) -> int | None:
    """13 para 'Semana 13 - Foundation for Intelligent Physical Agents'."""
    return _numero(_SEMANA, titulo)


class SinIdentidad(RuntimeError):
    """El endpoint de identidad no respondió: sin él no se sabe qué cursos pedir."""


def cursos_matriculados(get) -> list[dict]:
    """Los cursos del usuario, ya aplanados. Dos GET: identidad y matrículas."""
    yo = get("/learn/api/public/v1/users/me")
    if not yo or not yo.get("id"):
        raise SinIdentidad

    respuesta = get(f"/learn/api/public/v1/users/{yo['id']}/courses?expand=course&limit=100")
    return _cursos.normalizar((respuesta or {}).get("results", []))


def api_curso(course_id: str) -> str:
    return f"/learn/api/public/v1/courses/{course_id}"


def _adjuntos_de_archivo(get, base: str, item: dict) -> list[dict]:
    """Los adjuntos de un item `x-bb-file`, ya con su URL de descarga."""
    respuesta = get(f"{base}/contents/{item['id']}/attachments")
    adjuntos = []
    for att in (respuesta or {}).get("results", []):
        adjuntos.append({
            "name": att.get("fileName") or item.get("title"),
            "url": f"{BASE_API}{base}/contents/{item['id']}/attachments/{att['id']}/download",
            "status": "pending",
        })
    return adjuntos


def recorrer(get, course_id: str) -> list[dict]:
    """Devuelve los documentos con contenido, en la forma que espera el manifiesto.

    La unidad y la semana se heredan de las carpetas ancestro: el árbol es
    'Unidad N' → 'Semana M' → documento.
    """
    base = api_curso(course_id)
    documentos: list[dict] = []
    # Los archivos sueltos de una misma carpeta son UN documento: comparten la
    # página navegable, y emitirlos por separado los duplicaría en el merge.
    por_carpeta: dict[str, dict] = {}

    def bajar(item: dict, unidad: int | None, semana: int | None, titulo_padre: str) -> None:
        crudo = (item.get("title") or "").strip()
        unidad = numero_de_unidad(crudo) or unidad
        semana = numero_de_semana(crudo) or semana
        # Un documento sin título propio hereda el de la carpeta que lo contiene, que
        # es lo que el usuario ve en Ultra.
        titulo = titulo_padre if crudo in TITULOS_PLACEHOLDER else crudo

        handler = item.get("contentHandler", {}).get("id")
        if handler == HANDLER_ARCHIVO:
            adjuntos = _adjuntos_de_archivo(get, base, item)
            if adjuntos:
                pagina = item.get("parentId") or item["id"]
                url = f"{BASE_UI}/{course_id}/document/{pagina}?view=content&state=view"
                doc = por_carpeta.get(url)
                if doc is None:
                    doc = {
                        "unit": unidad,
                        "week": semana,
                        # El título del item ES el nombre del archivo; el que sirve
                        # al lector es el de la carpeta que lo agrupa.
                        "title": titulo_padre or titulo,
                        "source_url": url,
                        "attachments": [],
                        "external_links": [],
                    }
                    por_carpeta[url] = doc
                    documentos.append(doc)
                doc["attachments"].extend(adjuntos)

        if handler == HANDLER_DOCUMENTO:
            extraido = contenido_del_body(item.get("body"))
            if extraido["attachments"] or extraido["external_links"]:
                # La página navegable en Ultra es la CARPETA que contiene al documento,
                # no la hoja. Es la URL que el usuario abre y la que el manifiesto ya
                # registra; usar el id de la hoja hace que merge no reconozca nada.
                pagina = item.get("parentId") or item["id"]
                documentos.append({
                    "unit": unidad,
                    "week": semana,
                    "title": titulo,
                    "source_url": f"{BASE_UI}/{course_id}/document/{pagina}?view=content&state=view",
                    **extraido,
                })

        hijos = get(f"{base}/contents/{item['id']}/children")
        for hijo in (hijos or {}).get("results", []):
            bajar(hijo, unidad, semana, titulo)

    raiz = get(f"{base}/contents")
    for item in (raiz or {}).get("results", []):
        bajar(item, None, None, "")

    return documentos
