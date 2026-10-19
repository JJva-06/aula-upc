"""Lectura, migración y merge de manifest.json.

El esquema es abierto: se valida y modifica solo el núcleo, y todo lo demás
se preserva intacto en cualquier nivel de anidamiento.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

SCHEMA_VERSION = 2


def migrate(data: dict) -> dict:
    """Lleva un manifiesto a schema v2 sin perder ninguna clave desconocida."""
    out = copy.deepcopy(data)

    # v2 tiene una sola lista de documentos. Lo que v1 dejaba en additional_items
    # se pliega con week=None; si no, sus adjuntos quedarían fuera del alcance de
    # discover y sync, y nunca se descargarían.
    extras = out.pop("additional_items", [])
    if extras:
        documentos = out.setdefault("documents", [])
        for extra in extras:
            extra.setdefault("week", None)
            extra.setdefault("unit", None)
            documentos.append(extra)

    for doc in out.get("documents", []):
        for att in doc.get("attachments", []):
            if att.get("status") == "blocked_by_browser_policy":
                att["status"] = "pending"
    out["schema_version"] = SCHEMA_VERSION
    return out


def merge(existing: dict, discovered: list[dict]) -> dict:
    """Incorpora documentos descubiertos sin pisar lo que el usuario editó a mano."""
    out = copy.deepcopy(existing)
    docs = out.setdefault("documents", [])
    por_url = {d.get("source_url"): d for d in docs}

    for nuevo in discovered:
        url = nuevo.get("source_url")
        if url not in por_url:
            copia = copy.deepcopy(nuevo)
            docs.append(copia)
            # El índice también cubre lo recién añadido: si el mismo lote trae dos
            # hallazgos de la misma página, son un documento, no dos.
            por_url[url] = copia
            continue
        _merge_attachments(por_url[url], nuevo)
    return out


# La URL de descarga es transporte, no estado: las de bbcswebdav van firmadas y
# caducan, así que la recién descubierta pisa a la guardada. Es lo único que pisa.
CAMPOS_DE_TRANSPORTE = ("url",)


def _merge_attachments(destino: dict, origen: dict) -> None:
    """Añade los adjuntos que falten y completa los campos ausentes de los que ya están.

    Salvo la URL, nunca pisa un valor existente: lo que el usuario editó o lo que sync
    ya escribió (status, sha256, local_path) manda sobre lo que traiga el descubrimiento.
    """
    adjuntos = destino.setdefault("attachments", [])
    por_nombre = {a.get("name"): a for a in adjuntos}

    for att in origen.get("attachments", []):
        existente = por_nombre.get(att.get("name"))
        if existente is None:
            nuevo = copy.deepcopy(att)
            nuevo.setdefault("status", "pending")
            adjuntos.append(nuevo)
            continue
        for clave, valor in att.items():
            # Un descubrimiento sin URL (un enlace, un adjunto que Ultra dejó de
            # servir) no debe borrar la única que se tenía.
            if clave in CAMPOS_DE_TRANSPORTE and valor:
                existente[clave] = valor
            else:
                existente.setdefault(clave, valor)


def load(path) -> dict:
    """Lee un manifiesto y lo migra a v2 si hace falta."""
    # `utf-8-sig`: el Bloc de notas de Windows puede guardar el JSON con BOM.
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if data.get("schema_version", 1) < SCHEMA_VERSION:
        return migrate(data)
    return data


def save(path, data: dict) -> None:
    """Escribe el manifiesto con diffs de git legibles.

    Python preserva el orden de inserción de los dicts, así que respetar el orden
    existente sale gratis mientras no se reconstruyan los dicts.

    Siempre con `\\n`: si Windows lo escribiera con `\\r\\n`, un manifiesto hecho en
    Linux saldría entero en el diff.
    """
    texto = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    Path(path).write_text(texto, encoding="utf-8", newline="\n")


def resumen(data: dict) -> dict[str, int]:
    """Cuenta adjuntos por estado."""
    cuenta: dict[str, int] = {}
    for doc in data.get("documents", []):
        for att in doc.get("attachments", []):
            estado = att.get("status", "pending")
            cuenta[estado] = cuenta.get(estado, 0) + 1
    return cuenta
