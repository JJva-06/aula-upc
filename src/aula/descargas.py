"""Decisiones de `sync`: dónde va cada archivo y cuál hace falta bajar.

Funciones sobre el manifiesto y el disco: no tocan la red. Cuando hace falta abrir
una URL, quien llama inyecta la función que la abre.
"""

from __future__ import annotations

import copy
import hashlib
import os
import posixpath
import re
import unicodedata
from pathlib import Path

RAIZ_MATERIAL = "materials"
EN_WINDOWS = os.name == "nt"

# Lo que Windows no admite en un nombre de archivo. Se limpia en todos los sistemas:
# el manifiesto se commitea, y una ruta registrada en Linux tiene que servir en Windows.
_PROHIBIDOS = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def nombre_portable(nombre: str) -> str:
    """El nombre con lo que Windows rechaza cambiado por `_`.

    Fuera los caracteres prohibidos y los puntos y espacios del final (Windows los
    descarta en silencio y el archivo acabaría con otro nombre); a un nombre
    reservado (`CON`, `NUL.pdf`…) se le antepone `_`.
    """
    limpio = _PROHIBIDOS.sub("_", nombre).rstrip(". ")
    if limpio.split(".", 1)[0].upper() in _RESERVADOS:
        limpio = f"_{limpio}"
    return limpio or "_"


def ruta_portable(ruta: str) -> bool:
    """¿Cada tramo de la ruta (con `/`) es un nombre válido también en Windows?"""
    return all(tramo == nombre_portable(tramo) for tramo in ruta.split("/"))


def destino_local(week: int | None, nombre: str) -> str:
    """Ruta relativa donde vive un adjunto dentro del repo del curso."""
    carpeta = f"week-{week:02d}" if isinstance(week, int) else "extra"
    # El nombre viene del Aula Virtual: nunca se deja que escape de su carpeta.
    seguro = nombre_portable(posixpath.basename(nombre.replace("\\", "/")))
    return f"{RAIZ_MATERIAL}/{carpeta}/{seguro}"


def slug(texto: str, tope: int = 40) -> str:
    """El texto reducido a algo que quepa en un nombre de archivo, sin tildes ni signos."""
    plano = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plano.lower()).strip("-")[:tope].strip("-")


def _candidatos(base: str, titulo: str):
    """Rutas a probar, en orden, para un adjunto cuya ruta natural ya está ocupada.

    Primero el título del documento, que le dice a quien estudia de cuál de los dos
    salió; un número solo si eso tampoco alcanza.
    """
    yield base
    carpeta, _, archivo = base.rpartition("/")
    tallo, extension = os.path.splitext(archivo)
    marca = slug(titulo)
    raiz = f"{tallo}-{marca}" if marca else tallo
    if marca:
        yield f"{carpeta}/{raiz}{extension}"
    for n in range(2, 100):
        yield f"{carpeta}/{raiz}-{n}{extension}"


def asignar_destinos(data: dict) -> dict:
    """Fija el `local_path` de cada adjunto sin que dos se pisen.

    Dos documentos de la misma semana pueden traer adjuntos con el mismo nombre y
    URLs distintas (la Sesión y el Laboratorio de la semana 11 de Concurrente), y con
    una sola ruta el segundo borra al primero.

    Un `local_path` ya registrado se respeta, para que las rutas no bailen entre
    corridas ni se huerfanen los archivos que el repo del curso ya tiene. Solo se
    reasigna el segundo de dos adjuntos que reclamen la misma ruta: ahí es seguro
    que uno de los dos archivos no está en disco.

    En Windows tampoco se respeta una ruta que allí no puede existir (un `:` que
    dejó pasar un manifiesto hecho en Linux): ese archivo seguro que no está en disco.
    """
    out = copy.deepcopy(data)
    ocupadas: set[str] = set()

    for doc in out.get("documents", []):
        for att in doc.get("attachments", []):
            registrada = att.get("local_path")
            if registrada and EN_WINDOWS and not ruta_portable(registrada):
                registrada = None
            if registrada and registrada not in ocupadas:
                ocupadas.add(registrada)
                continue

            base = destino_local(doc.get("week"), att.get("name", ""))
            for ruta in _candidatos(base, doc.get("title", "")):
                if ruta not in ocupadas:
                    break
            att["local_path"] = ruta
            ocupadas.add(ruta)

    return out


def sha256_de(archivo: Path, trozo: int = 1 << 20) -> str:
    """El digest del archivo, leído por trozos para no cargarlo entero en memoria."""
    suma = hashlib.sha256()
    with open(archivo, "rb") as f:
        while bloque := f.read(trozo):
            suma.update(bloque)
    return suma.hexdigest()


def necesita_descarga(att: dict, raiz: Path) -> bool:
    """¿Hay que (re)bajar este adjunto?

    Se rebaja lo que falta en disco, no cuadra en tamaño o no cuadra con el digest
    que sync registró; no se reintenta lo que quedó marcado como no disponible, ni
    lo que `aula respaldo --liberar` borró tras verificar que Drive tiene esa versión.
    """
    if att.get("status") == "unavailable":
        return False
    digest = att.get("sha256")
    if att.get("offloaded") and digest and (att.get("backup") or {}).get("sha256") == digest:
        return False
    if att.get("status") != "downloaded":
        return True

    ruta_rel = att.get("local_path")
    if not ruta_rel:
        return True

    archivo = Path(raiz) / ruta_rel
    if not archivo.exists():
        return True

    esperado = att.get("size_bytes")
    if esperado is not None and archivo.stat().st_size != esperado:
        return True

    # El tamaño es la criba barata; el digest es el que caza una corrupción que
    # no cambió la longitud. Los manifiestos v1 migrados no traen digest.
    return bool(digest) and sha256_de(archivo) != digest


def guardar_flujo(abrir, url: str, destino: Path, trozo: int = 1 << 20) -> tuple[int, str]:
    """Baja `url` a `destino` por trozos y devuelve (tamaño, sha256).

    `abrir(url)` devuelve un objeto con `read(n)` usable como context manager (la
    respuesta de urlopen). Se escribe a un `.parcial` y se renombra al final: un
    corte de red nunca deja un archivo a medias que parezca descargado.
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_name(destino.name + ".parcial")
    suma = hashlib.sha256()
    tamano = 0
    try:
        with abrir(url) as flujo, open(temporal, "wb") as salida:
            while bloque := flujo.read(trozo):
                salida.write(bloque)
                suma.update(bloque)
                tamano += len(bloque)
        temporal.replace(destino)
    except BaseException:
        temporal.unlink(missing_ok=True)
        raise
    return tamano, suma.hexdigest()
