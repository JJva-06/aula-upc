"""Grabaciones de clase: listarlas, decidir qué bajar y dónde, y registrarlas.

Las grabaciones NO están en la API REST de Blackboard. Viven en Class (Class
Technologies), que Ultra integra por LTI 1.3 desde el panel «Launch Class».

Todo aquí es puro: recibe el JSON que devolvió Class y no toca red ni navegador.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .descargas import necesita_descarga, slug

RAIZ = "materials/grabaciones"

# Lima no tiene horario de verano: UTC-5 fijo todo el año. Así no hace falta tzdata.
LIMA = timezone(timedelta(hours=-5))

# Tipo de artefacto de Class → sufijo legible y extensión del archivo local.
ARTEFACTOS = {
    "audio_transcript": ("transcripcion", "vtt"),
    "closed_caption": ("subtitulos", "vtt"),
    "active_speaker": ("video", "mp4"),
    "shared_screen": ("pantalla", "mp4"),
}
# `timeline` existe pero no se ofrece: es un JSON de varios MB con el correo de
# cada participante, y no aporta nada a quien estudia la clase.

CLAVE_MANIFIESTO = "class_recordings"

_UUID_CLASE = re.compile(r"/react/lti/([0-9a-f-]{36})")


class ListaNoDisponible(RuntimeError):
    """Class no devolvió el listado. Un curso sin grabaciones responde 200 con una
    lista vacía, así que la ausencia de respuesta es un fallo, no un curso vacío."""



def ruta_lista(school: str, clase: str) -> str:
    return f"/recording/v1/schools/{school}/class/{clase}/recordings"


def ruta_detalle(school: str, clase: str, grabacion: str) -> str:
    return f"{ruta_lista(school, clase)}/{grabacion}"


def ruta_artefactos(school: str, clase: str, grabacion: str, tipo: str) -> str:
    return f"{ruta_detalle(school, clase, grabacion)}/artifacts?contents_list={tipo}"


def class_uuid_de_url(url: str) -> str | None:
    """El uuid de la clase de Class, sacado de la URL del iframe que abre el LTI."""
    m = _UUID_CLASE.search(url or "")
    return m.group(1) if m else None


def parsear_lista(respuesta: dict | None) -> list[dict]:
    """Las grabaciones que ya se pueden ver, en la forma que usa el manifiesto."""
    if respuesta is None:
        raise ListaNoDisponible
    grabaciones = []
    for r in respuesta.get("recordings", []):
        if r.get("status") != "AVAILABLE" or not r.get("recordingId"):
            continue
        grabaciones.append({
            "recording_id": r["recordingId"],
            "when": r.get("when"),
            "name": r.get("name") or "",
            "duration_s": r.get("duration"),
        })
    return grabaciones


def disponibles(detalle: dict | None) -> set[str]:
    """Los tipos de artefacto que Class tiene para una grabación."""
    return set(((detalle or {}).get("artifacts") or {}).keys())


def url_de_artefacto(respuesta: dict | None, tipo: str) -> str | None:
    """La URL firmada de CloudFront para un artefacto. Caduca en 12 horas."""
    for a in (respuesta or {}).get("artifacts", []):
        if a.get("contents") == tipo and a.get("url"):
            return a["url"]
    return None


def tipos_pedidos(video: bool, pantalla: bool) -> list[str]:
    """Las transcripciones siempre; los .mp4 solo si se piden, porque pesan cientos de MB."""
    tipos = ["audio_transcript", "closed_caption"]
    if video:
        tipos.append("active_speaker")
    if pantalla:
        tipos.append("shared_screen")
    return tipos


def nombre_base(when: str, name: str) -> str:
    """'2026-09-07_19h00' (hora de Lima), con el título detrás si la grabación tiene uno."""
    instante = datetime.fromisoformat(when).astimezone(LIMA)
    base = instante.strftime("%Y-%m-%d_%Hh%M")
    marca = slug(name or "")
    return f"{base}_{marca}" if marca else base


def ruta_de(base: str, tipo: str) -> str:
    sufijo, extension = ARTEFACTOS[tipo]
    return f"{RAIZ}/{base}.{sufijo}.{extension}"


def destino(when: str, name: str, tipo: str) -> str:
    return ruta_de(nombre_base(when, name), tipo)


def nombres_base(grabaciones: list[dict]) -> dict[str, str]:
    """El nombre de archivo de cada grabación, ya desambiguado, por `recording_id`.

    Class no garantiza títulos ni minutos distintos: una sesión que se corta y se
    reanuda deja dos grabaciones sin `name` que empiezan en el mismo minuto, y el
    nombre por hora las mandaría al mismo archivo. Cuando un nombre lo comparten
    dos o más, TODAS llevan detrás su id: así el reparto no depende del orden en
    que Class devuelva la lista.
    """
    base = {r["recording_id"]: nombre_base(r["when"], r.get("name", "")) for r in grabaciones}
    repetidos = {n for n, veces in Counter(base.values()).items() if veces > 1}
    return {rid: f"{n}_{rid[:8]}" if n in repetidos else n for rid, n in base.items()}


def registros(grabaciones: list[dict], tipos: list[str]) -> list[dict]:
    """Las entradas de manifiesto de una lista, con los nombres ya desambiguados
    entre sí. Es la forma de registrar varias: `registro` sola no ve a las vecinas."""
    nombres = nombres_base(grabaciones)
    return [registro(g, tipos, nombres[g["recording_id"]]) for g in grabaciones]


def registro(grabacion: dict, tipos: list[str], base: str | None = None) -> dict:
    """La entrada de manifiesto de una grabación, con sus artefactos pendientes."""
    base = base or nombre_base(grabacion["when"], grabacion.get("name", ""))
    artefactos = []
    for tipo in tipos:
        ruta = ruta_de(base, tipo)
        artefactos.append({
            "kind": tipo,
            "name": ruta.rsplit("/", 1)[-1],
            "local_path": ruta,
            "status": "pending",
        })
    return {**grabacion, "artifacts": artefactos}


def merge(manifiesto: dict, registros: list[dict]) -> dict:
    """Incorpora grabaciones sin pisar nada de lo ya registrado.

    Van en su propia clave: `recordings` ya existe en manifiestos v1 con otra forma
    (enlaces al visor antiguo) y el esquema abierto obliga a respetarla.
    """
    out = copy.deepcopy(manifiesto)
    lista = out.setdefault(CLAVE_MANIFIESTO, [])
    por_id = {r.get("recording_id"): r for r in lista}

    for nuevo in registros:
        existente = por_id.get(nuevo["recording_id"])
        if existente is None:
            copia = copy.deepcopy(nuevo)
            lista.append(copia)
            por_id[copia["recording_id"]] = copia
            continue
        for clave, valor in nuevo.items():
            if clave != "artifacts":
                existente.setdefault(clave, valor)
        artefactos = existente.setdefault("artifacts", [])
        tipos = {a.get("kind") for a in artefactos}
        for art in nuevo.get("artifacts", []):
            if art["kind"] not in tipos:
                artefactos.append(copy.deepcopy(art))
    return out


def pendientes(grabacion: dict, raiz: Path) -> list[dict]:
    """Los artefactos de una grabación que hay que (re)bajar."""
    return [a for a in grabacion.get("artifacts", []) if necesita_descarga(a, Path(raiz))]


_TIEMPO = re.compile(r"^(\d{2}:\d{2}:\d{2})\.\d{3}\s+-->")
_HABLANTE = re.compile(r"^([^:]{2,80}):\s+(.*)$")


def vtt_a_texto(vtt: str) -> str:
    """Transcripción en prosa: un párrafo por turno de palabra, con su marca de tiempo."""
    parrafos: list[list] = []  # [hablante, inicio, [frases]]
    for bloque in vtt.replace("\r\n", "\n").strip().split("\n\n"):
        lineas = bloque.split("\n")
        idx = next((i for i, linea in enumerate(lineas) if _TIEMPO.match(linea)), None)
        if idx is None:
            continue  # la cabecera WEBVTT o un bloque NOTE
        inicio = _TIEMPO.match(lineas[idx]).group(1)
        texto = " ".join(linea.strip() for linea in lineas[idx + 1:] if linea.strip())
        if not texto:
            continue
        m = _HABLANTE.match(texto)
        hablante, frase = (m.group(1), m.group(2)) if m else (None, texto)

        if parrafos and parrafos[-1][0] == hablante:
            parrafos[-1][2].append(frase)
        else:
            parrafos.append([hablante, inicio, [frase]])

    salida = []
    for hablante, inicio, frases in parrafos:
        cuerpo = " ".join(frases)
        salida.append(f"[{inicio}] {hablante}: {cuerpo}" if hablante else f"[{inicio}] {cuerpo}")
    return "\n\n".join(salida) + "\n" if salida else ""
