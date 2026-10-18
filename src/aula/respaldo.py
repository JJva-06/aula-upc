"""Respaldo de `materials/`: qué se sube, a dónde, y qué se puede liberar del disco.

`materials/` no se versiona en los repos de curso (son públicos y el material es del
profesor), así que sin esto la única copia vive en la laptop, y la receta del
manifiesto deja de servir el día que Blackboard cierra el curso.

La red la pone rclone, con su propia config (`rclone config`): aula no toca OAuth
ni guarda credenciales, y el proveedor le da igual (OneDrive, Google Drive, un
bucket de HF por su API S3). Lo único propio de HF es comprobar que el bucket sea
privado, porque allí se crean públicos si no se dice lo contrario.

Aquí se decide y se registra. Quien llama inyecta con qué se ejecuta rclone y con
qué se consulta a HF (`correr` y `sondear` en uso real, falsos en los tests).
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .descargas import RAIZ_MATERIAL, sha256_de
from .grabaciones import CLAVE_MANIFIESTO

# Solo los .mp4 de Class se liberan: pesan cientos de MB por clase. Las
# transcripciones y los adjuntos del curso son pequeños y conviene tenerlos a mano.
LIBERABLES = {"active_speaker", "shared_screen"}

# El gateway S3 de los buckets de HF y la API que dice, sin token, si uno es público.
HOST_HF = "s3.hf.co"
API_BUCKETS = "https://huggingface.co/api/buckets"

INSTALAR = ("Instálalo con el instalador oficial: `sudo -v ; curl https://rclone.org/install.sh | sudo bash`. "
            "La versión de apt (1.60) no alcanza para comprobar buckets de HF.")


class SinDestino(ValueError):
    """El manifiesto no dice qué curso es, así que no hay carpeta en el remoto."""


class FaltaRemoto(SinDestino):
    """Nadie dijo a qué remoto subir: ni --remoto, ni $AULA_RESPALDO, ni el manifiesto."""


class RcloneNoDisponible(RuntimeError):
    """rclone no está instalado, no tiene el remoto o es demasiado viejo."""


class RcloneFallo(RuntimeError):
    """rclone corrió y falló, no cabe lo pendiente, o no se pudo comprobar algo."""


class BucketPublico(RcloneFallo):
    """El bucket de HF de destino se ve sin token: subir ahí sería publicar el material."""


def remoto_base(env: dict | None = None) -> str | None:
    """El remoto de $AULA_RESPALDO. No hay uno por defecto: se elige a conciencia."""
    env = os.environ if env is None else env
    return env.get("AULA_RESPALDO") or None


def destino(data: dict, base: str | None) -> str:
    """La carpeta del curso en el remoto: `<base>/<code>`.

    Una vez registrada en el manifiesto se respeta, como `local_path`: si cambia el
    código del curso o el remoto base, lo ya subido no queda huérfano en otra ruta.
    """
    registrado = (data.get("backup") or {}).get("remote")
    if registrado:
        return registrado
    if not base:
        raise FaltaRemoto(
            "falta el remoto. La primera vez corre `aula respaldo --remoto <remoto>:<carpeta>` "
            "(p. ej. `hf:aula` u `onedrive:aula`) o define $AULA_RESPALDO"
        )
    curso = data.get("course") or {}
    carpeta = curso.get("code") or curso.get("blackboard_course_id")
    if not carpeta:
        raise SinDestino("el manifiesto no tiene `course.code` ni `course.blackboard_course_id`")
    return f"{base}{carpeta}" if base.endswith(":") else f"{base.rstrip('/')}/{carpeta}"


def nombre_remoto(destino_: str) -> str:
    """'hf:aula/1ACC0065' → 'hf:', como lo lista `rclone listremotes`."""
    return destino_.split(":", 1)[0] + ":"


def _origen(raiz: Path) -> str:
    return str(Path(raiz) / RAIZ_MATERIAL)


def _en_remoto(destino_: str) -> str:
    return f"{destino_}/{RAIZ_MATERIAL}"


def _reemplazados(destino_: str, sello: str) -> str:
    """Adonde va lo que el remoto tenía y una subida pisa. Hermana de `materials/`:
    rclone no acepta un `--backup-dir` dentro del destino."""
    return f"{destino_}/_reemplazados/{sello}"


def archivos_a_subir(raiz: Path, excluidos: list[str]) -> list[str]:
    """Todo `materials/`, relativo a esa carpeta, menos las descargas a medias
    (`.parcial`) y lo excluido (rutas del manifiesto). Entra también lo que no está
    en el manifiesto: el sílabo, las transcripciones en prosa.

    Es una lista literal para `--files-from`: con patrones de `--exclude`, un nombre
    con `[` o `*` haría de comodín.
    """
    base = Path(raiz) / RAIZ_MATERIAL
    fuera = set(_relativas(excluidos))
    return sorted(
        rel
        for archivo in base.rglob("*")
        if archivo.is_file() and not archivo.name.endswith(".parcial")
        and (rel := archivo.relative_to(base).as_posix()) not in fuera
    )


def comando_copiar(raiz: Path, destino_: str, lista: str, sello: str) -> list[str]:
    """La copia incremental de lo que dice `lista`.

    Se decide por fecha y no por hash: la API S3 de HF descarta los metadatos donde
    rclone guarda la fecha y el MD5, así que con `--checksum` acabaría comparando
    tamaños sin avisar. Con la fecha del servidor, un archivo rebajado (más nuevo que
    su copia) se vuelve a subir y lo demás se salta sin leer GB en cada corrida.

    `--backup-dir`: lo que el remoto tenía y se pisa no se destruye.
    `--s3-no-check-bucket`: rclone nunca crea el bucket. Uno creado así podría quedar
    público, y un error de tipeo en el nombre no debe publicar nada.
    """
    return ["rclone", "copy", _origen(raiz), _en_remoto(destino_), "--files-from", lista,
            "--update", "--use-server-modtime", "--backup-dir", _reemplazados(destino_, sello),
            "--s3-no-check-bucket", "-v", "--stats-one-line"]


def _entradas(data: dict):
    for doc in data.get("documents", []):
        yield from doc.get("attachments", [])
    for rec in data.get(CLAVE_MANIFIESTO, []):
        yield from rec.get("artifacts", [])


def _en_disco(att: dict, raiz: Path) -> Path | None:
    if att.get("status") != "downloaded" or not att.get("local_path"):
        return None
    archivo = Path(raiz) / att["local_path"]
    return archivo if archivo.is_file() else None


def _respaldado(att: dict) -> bool:
    """¿El remoto tiene la versión que describe el manifiesto?"""
    digest = att.get("sha256")
    return bool(digest) and (att.get("backup") or {}).get("sha256") == digest


def _cambio_tras_subirse(att: dict, archivo: Path, tolerancia_s: float = 2) -> bool:
    """¿El archivo se escribió después de su última subida? Sin fecha legible, sí."""
    try:
        subido = datetime.fromisoformat(att["backup"]["uploaded_at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return True
    return archivo.stat().st_mtime > subido + tolerancia_s


def alterados(data: dict, raiz: Path) -> list[str]:
    """Lo ya respaldado que cambió en disco y ya no es la versión del manifiesto.

    No se sube: pisaría la copia buena del remoto, y con el disco y el remoto
    corruptos la única versión buena quedaría en el Aula, si aún está. Solo se lee
    lo que se escribió después de subirse, así que una corrida normal no lee nada.
    """
    return [
        att["local_path"]
        for att in _entradas(data)
        if (archivo := _en_disco(att, raiz)) is not None and _respaldado(att)
        and _cambio_tras_subirse(att, archivo) and sha256_de(archivo) != att["sha256"]
    ]


def marcar_subidos(data: dict, raiz: Path, destino_: str, ahora: str) -> dict:
    """Anota en cada entrada que está en disco qué versión quedó en el remoto.

    Se llama solo tras un `rclone copy` que terminó bien, y se anota el digest del
    archivo que se subió, no el que dice el manifiesto: si el disco tenía una copia
    corrupta, la marca no coincide con el manifiesto y ese video nunca se libera.

    Lo respaldado que no cambió no se vuelve a leer: dos corridas seguidas no tocan
    el manifiesto ni hashean GB. Si cambió y sigue siendo la versión del manifiesto
    (`sync` lo bajó de nuevo), se subió otra vez y se refresca la fecha; si ya no lo
    es, es un alterado: no se subió y la marca sigue describiendo la copia buena.
    """
    out = copy.deepcopy(data)
    out.setdefault("backup", {}).setdefault("remote", destino_)
    for att in _entradas(out):
        archivo = _en_disco(att, raiz)
        if archivo is None:
            continue
        if _respaldado(att):
            if _cambio_tras_subirse(att, archivo) and sha256_de(archivo) == att["sha256"]:
                att["backup"] = {"sha256": att["sha256"], "uploaded_at": ahora}
            continue
        digest = sha256_de(archivo)
        if (att.get("backup") or {}).get("sha256") != digest:
            att["backup"] = {"sha256": digest, "uploaded_at": ahora}
    return out


def bytes_por_subir(data: dict, raiz: Path) -> int:
    """Lo que ocupa lo que está en disco y el remoto aún no tiene en esa versión."""
    return sum(
        archivo.stat().st_size
        for att in _entradas(data)
        if (archivo := _en_disco(att, raiz)) is not None and not _respaldado(att)
    )


def libre_de(about: dict) -> int | None:
    """El espacio libre según `rclone about --json`. None si el backend no lo dice."""
    return about.get("free")


def liberables(data: dict) -> list[dict]:
    """Los videos que el manifiesto da por respaldados y aún no se liberaron."""
    return [
        art
        for rec in data.get(CLAVE_MANIFIESTO, [])
        for art in rec.get("artifacts", [])
        if art.get("kind") in LIBERABLES and not art.get("offloaded") and _respaldado(art)
    ]


def liberar(data: dict, raiz: Path, confirmados: set[str]) -> tuple[dict, int]:
    """Borra del disco los liberables confirmados y los marca `offloaded`. Devuelve
    cuántos bytes se liberaron. `confirmados` sale de `liberar_verificados`."""
    out = copy.deepcopy(data)
    liberados = 0
    for art in liberables(out):
        if art["local_path"] not in confirmados:
            continue
        archivo = Path(raiz) / art["local_path"]
        if archivo.is_file():
            liberados += archivo.stat().st_size
            archivo.unlink()
        art["offloaded"] = True
    return out, liberados


def resumen(data: dict) -> dict[str, int]:
    entradas = list(_entradas(data))
    return {
        "respaldados": sum(1 for a in entradas if _respaldado(a)),
        "liberados": sum(1 for a in entradas if a.get("offloaded")),
    }


# --- ¿Es privado el bucket? ---------------------------------------------------------


def tipos_de_remotos(salida: str) -> dict[str, str]:
    """`rclone listremotes --long` → {'hf:': 's3', 'gdrive:': 'drive'}."""
    tipos = {}
    for linea in salida.splitlines():
        partes = linea.split()
        if partes and partes[0].endswith(":"):
            tipos[partes[0]] = partes[1] if len(partes) > 1 else ""
    return tipos


def endpoint_de(config_redactada: str) -> str | None:
    """El `endpoint` de la salida de `rclone config redacted <remoto>`."""
    for linea in config_redactada.splitlines():
        clave, _, valor = linea.partition("=")
        if clave.strip() == "endpoint" and valor.strip():
            return valor.strip()
    return None


def bucket_hf(endpoint: str, destino_: str) -> str | None:
    """'namespace/bucket' si el remoto apunta al gateway de HF; None si no es de HF.

    HF admite dos formas: el namespace en el endpoint (`https://s3.hf.co/<ns>`, la
    recomendada) y el namespace como primer segmento del bucket.
    """
    url = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}")
    if (url.hostname or "").lower() != HOST_HF:
        return None
    segmentos = [s for s in url.path.split("/") if s][:1]
    segmentos += [s for s in destino_.split(":", 1)[1].split("/") if s]
    if len(segmentos) < 2:
        raise RcloneFallo(f"no sé qué bucket de HF es «{destino_}»: falta el namespace o el nombre del bucket")
    return f"{segmentos[0]}/{segmentos[1]}"


def url_sonda(bucket: str) -> str:
    return f"{API_BUCKETS}/{bucket}"


def es_publico(status: int, cuerpo: str) -> bool:
    """Sin token, HF solo muestra un bucket si es público; privado o inexistente da 401."""
    if status != 200:
        return False
    try:
        return json.loads(cuerpo).get("private") is not True
    except ValueError:
        return True  # se vio sin token: se trata como público


# --- Capa fina sobre rclone y HF ---------------------------------------------------
# `ejecutar(cmd, capturar)` devuelve (código de salida, stdout). Con capturar=False
# la salida de rclone va directo a la terminal: una subida de videos tarda minutos.


def correr(cmd: list[str], capturar: bool = True) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=capturar, text=True, check=False)
    return r.returncode, r.stdout or ""


def sondear(url: str) -> tuple[int, str]:
    """GET sin token: lo que vería cualquiera en internet."""
    req = urllib.request.Request(url, headers={"User-Agent": "aula"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def comprobar_remoto(ejecutar, destino_: str) -> str:
    """Que rclone esté y conozca el remoto. Devuelve su tipo (`s3`, `onedrive`…)."""
    nombre = nombre_remoto(destino_)
    try:
        codigo, salida = ejecutar(["rclone", "listremotes", "--long"])
    except FileNotFoundError:
        raise RcloneNoDisponible(f"rclone no está instalado. {INSTALAR}") from None
    tipos = tipos_de_remotos(salida) if codigo == 0 else {}
    if nombre not in tipos:
        raise RcloneNoDisponible(
            f"rclone no tiene un remoto «{nombre}». Créalo con `rclone config` o elige otro con --remoto."
        )
    return tipos[nombre]


def comprobar_privado(ejecutar, sondear_, destino_: str, tipo: str) -> str | None:
    """Si el destino es un bucket de HF, que no se vea sin token. Devuelve el bucket
    (`namespace/bucket`), o None si el remoto no es de HF.

    Falla cerrado: si no se puede saber si es de HF o si es privado, no se sube.
    """
    if tipo != "s3":
        return None
    codigo, config = ejecutar(["rclone", "config", "redacted", nombre_remoto(destino_).rstrip(":")])
    if codigo != 0:
        raise RcloneNoDisponible(
            "tu rclone no puede mostrar la config sin las claves (`rclone config redacted` existe desde "
            f"la 1.64), así que no sé si el remoto es un bucket de HF ni si es privado. {INSTALAR}"
        )
    bucket = bucket_hf(endpoint_de(config) or "", destino_)
    if bucket is None:
        return None
    try:
        status, cuerpo = sondear_(url_sonda(bucket))
    except OSError as e:
        raise RcloneFallo(f"no pude preguntarle a HF si el bucket {bucket} es privado ({e}); no se sube nada.") from None
    if es_publico(status, cuerpo):
        raise BucketPublico(
            f"el bucket {bucket} es público: subir ahí sería publicar el material del curso. "
            f"Hazlo privado con `hf buckets settings {bucket} --private` y vuelve a correr."
        )
    return bucket


def espacio_libre(ejecutar, destino_: str) -> int | None:
    codigo, salida = ejecutar(["rclone", "about", nombre_remoto(destino_), "--json"])
    if codigo != 0:
        return None
    try:
        return libre_de(json.loads(salida))
    except ValueError:
        return None


def subir(ejecutar, raiz: Path, destino_: str, excluidos: list[str], sello: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lista = Path(tmp) / "archivos.txt"
        lista.write_text("".join(f"{r}\n" for r in archivos_a_subir(raiz, excluidos)), encoding="utf-8")
        codigo, _ = ejecutar(comando_copiar(raiz, destino_, str(lista), sello), capturar=False)
    if codigo != 0:
        raise RcloneFallo(f"rclone copy terminó con código {codigo}; no se marcó nada en el manifiesto.")


def _relativas(rutas: list[str]) -> dict[str, str]:
    """Ruta relativa a `materials/` (como las lista rclone) → ruta del manifiesto."""
    prefijo = f"{RAIZ_MATERIAL}/"
    return {r.removeprefix(prefijo): r for r in rutas}


def verificar(ejecutar, raiz: Path, destino_: str, rutas: list[str]) -> set[str]:
    """Las rutas (del manifiesto) que el remoto tiene idénticas, comparadas byte a byte.

    `--download` no depende de que el remoto tenga hashes ni de que su ETag sea un
    MD5. rclone sale con 1 si algo difiere; lo que vale es la lista de iguales.
    """
    relativas = _relativas(rutas)
    with tempfile.TemporaryDirectory() as tmp:
        lista, iguales = Path(tmp) / "archivos.txt", Path(tmp) / "iguales.txt"
        lista.write_text("\n".join(relativas) + "\n", encoding="utf-8")
        ejecutar(["rclone", "check", _origen(raiz), _en_remoto(destino_), "--one-way", "--download",
                  "--files-from", str(lista), "--match", str(iguales)], capturar=False)
        vistas = iguales.read_text(encoding="utf-8").splitlines() if iguales.exists() else []
    return {relativas[r] for r in vistas if r in relativas}


def reparar(ejecutar, raiz: Path, destino_: str, rutas: list[str], sello: str) -> bool:
    """Vuelve a subir estas rutas sin mirar fechas ni tamaños. Lo que había en el
    remoto no se destruye: va a `_reemplazados/`."""
    with tempfile.TemporaryDirectory() as tmp:
        lista = Path(tmp) / "archivos.txt"
        lista.write_text("\n".join(_relativas(rutas)) + "\n", encoding="utf-8")
        codigo, _ = ejecutar(["rclone", "copy", _origen(raiz), _en_remoto(destino_), "--files-from", str(lista),
                              "--ignore-times", "--backup-dir", _reemplazados(destino_, sello),
                              "--s3-no-check-bucket", "-v"], capturar=False)
    return codigo == 0


@dataclass
class Liberacion:
    data: dict
    liberados: list[str] = field(default_factory=list)
    bytes_liberados: int = 0
    corruptos: list[str] = field(default_factory=list)  # en disco no son la versión del manifiesto
    sin_confirmar: list[str] = field(default_factory=list)  # el remoto no los tiene idénticos


def liberar_verificados(ejecutar, data: dict, raiz: Path, destino_: str, sello: str) -> Liberacion:
    """Borra del disco, uno por uno, los videos que el remoto tiene idénticos.

    El orden importa. Primero se comprueba que el archivo en disco sea la versión del
    manifiesto: si no lo es, no se compara, no se resube y no se borra, porque
    resubirlo pisaría la copia buena del remoto con la corrupta y la comparación
    siguiente pasaría. Lo que el remoto no tiene idéntico se resube sin mirar fechas
    y se vuelve a comparar; lo que siga sin cuadrar se queda en disco.
    """
    sanos, corruptos = [], []
    for art in liberables(data):
        archivo = Path(raiz) / art["local_path"]
        intacto = archivo.is_file() and sha256_de(archivo) == art["sha256"]
        (sanos if intacto else corruptos).append(art["local_path"])

    confirmados = verificar(ejecutar, raiz, destino_, sanos) if sanos else set()
    fallidos = [r for r in sanos if r not in confirmados]
    if fallidos and reparar(ejecutar, raiz, destino_, fallidos, sello):
        confirmados |= verificar(ejecutar, raiz, destino_, fallidos)

    out, bytes_liberados = liberar(data, raiz, confirmados)
    return Liberacion(
        data=out,
        liberados=[r for r in sanos if r in confirmados],
        bytes_liberados=bytes_liberados,
        corruptos=corruptos,
        sin_confirmar=[r for r in sanos if r not in confirmados],
    )
