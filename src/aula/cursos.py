"""Los cursos en los que estás matriculado, y el manifiesto vacío de uno.

Todo aquí es puro: recibe el JSON que devolvió la API y no toca red ni disco,
para poder probarse contra un volcado real.
"""

from __future__ import annotations

BASE_UI = "https://aulavirtual.upc.edu.pe/ultra/courses"

# UPC cuelga la modalidad del nombre con un guion. Se compara contra una lista
# cerrada porque hay títulos con guiones propios que no deben perder su cola.
MODALIDADES = ("Virtual", "Presencial")


class CursoNoEncontrado(LookupError):
    """Ningún curso matriculado coincide con lo pedido."""


class CursoAmbiguo(LookupError):
    """Más de un curso coincide: hace falta el id de Blackboard."""


def parsear_external_id(external_id: str) -> dict:
    """'1ACC0202-2620-17495' -> código, term y NRC. {} si no tiene esa forma."""
    partes = (external_id or "").split("-")
    if len(partes) != 3:
        return {}
    code, term, nrc = partes
    return {"code": code, "term": term, "nrc": nrc}


def partir_nombre(name: str) -> tuple[str, str | None]:
    """'Complex Networks - Virtual' -> ('Complex Networks', 'Virtual')."""
    for modalidad in MODALIDADES:
        sufijo = f" - {modalidad}"
        if (name or "").endswith(sufijo):
            return name[: -len(sufijo)], modalidad
    return name, None


def normalizar(matriculas: list[dict]) -> list[dict]:
    """Aplana la respuesta de /users/{id}/courses?expand=course."""
    cursos = []
    for matricula in matriculas:
        curso = matricula.get("course") or {}
        cid = curso.get("id")
        if not cid:
            continue
        nombre, modalidad = partir_nombre(curso.get("name", ""))
        cursos.append(
            {
                "blackboard_course_id": cid,
                "name": nombre,
                "mode": modalidad,
                **parsear_external_id(curso.get("courseId", "")),
                "outline_url": curso.get("externalAccessUrl") or f"{BASE_UI}/{cid}/outline",
            }
        )
    return cursos


def elegir(cursos: list[dict], code: str | None = None, course_id: str | None = None) -> dict:
    """El curso que pediste por código o por id. Lanza si no hay uno solo."""
    if course_id:
        candidatos = [c for c in cursos if c.get("blackboard_course_id") == course_id]
    elif code:
        candidatos = [c for c in cursos if (c.get("code") or "").upper() == code.upper()]
    else:
        candidatos = []

    if not candidatos:
        raise CursoNoEncontrado(course_id or code or "sin criterio")
    if len(candidatos) > 1:
        raise CursoAmbiguo(code or course_id or "")
    return candidatos[0]


def esqueleto(curso: dict, ahora: str) -> dict:
    """El manifiesto v2 mínimo: solo lo que la API sabe de verdad.

    Nada de instructor, unidades ni grading: eso no vive en este endpoint y
    ponerlo en `null` solo ensuciaría el diff del primer commit.
    """
    bloque = {
        "name": curso.get("name"),
        "code": curso.get("code"),
        "term": curso.get("term"),
        "nrc": curso.get("nrc"),
        "blackboard_course_id": curso.get("blackboard_course_id"),
        "outline_url": curso.get("outline_url"),
    }
    if curso.get("mode"):
        bloque["mode"] = curso["mode"]

    return {
        "schema_version": 2,
        "generated_at": ahora,
        "course": bloque,
        "downloads": [],
        "documents": [],
    }
