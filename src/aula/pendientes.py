"""Las entregas de todos los cursos con su fecha y si ya se entregaron.

El calendario da las fechas pero no el estado. El estado que da el gradebook tampoco
basta: dice «NeedsGrading» cuando el grupo solo abrió un borrador, y «Graded» en una
columna que nadie ha entregado. Lo que manda son los intentos, y la nota si la hay.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# Lima no tiene horario de verano: UTC-5 fijo todo el año. Con ZoneInfo, Windows
# necesitaría el paquete tzdata, porque no trae la base de zonas horarias de IANA.
LIMA = timezone(timedelta(hours=-5))
# Sin depender del locale de la máquina, que aquí está en inglés.
DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
_CURSO = re.compile(r"^[^:]*:\s*(.*?)(?:\s+-\s+(?:Presencial|Virtual|Semipresencial|Blended))?\s*$")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _fecha(texto: str | None) -> datetime | None:
    return datetime.fromisoformat(texto) if texto else None


def ruta_calendario(desde: datetime, hasta: datetime) -> str:
    return (
        "/learn/api/public/v1/calendars/items"
        f"?since={_iso(desde)}&until={_iso(hasta)}&type=GradebookColumn&limit=200"
    )


def curso_corto(calendar_name: str) -> str:
    """'Complex Networks' para '1ACC0202-2620-17495: Complex Networks - Virtual'."""
    m = _CURSO.match(calendar_name or "")
    return m.group(1) if m else calendar_name


def estado(fila: dict | None, intentos: dict | None) -> dict:
    """Clasifica una columna: calificado, entregado, borrador o pendiente."""
    fila = fila or {}
    resultados = (intentos or {}).get("results", [])
    enviados = [a for a in resultados if a.get("status") != "InProgress"]
    enviado = _fecha(enviados[-1].get("created")) if enviados else None

    if fila.get("score") is not None:
        return {"estado": "calificado", "nota": fila["score"], "enviado": enviado}
    if enviados:
        return {"estado": "entregado", "nota": None, "enviado": enviado}
    if resultados:
        return {"estado": "borrador", "nota": None, "enviado": None}
    return {"estado": "pendiente", "nota": None, "enviado": None}


def listar(get, desde: datetime, hasta: datetime) -> list[dict]:
    """Las columnas con fecha en la ventana, cada una con su estado, por fecha."""
    yo = (get("/learn/api/public/v1/users/me") or {}).get("id")
    items = (get(ruta_calendario(desde, hasta)) or {}).get("results", [])
    filas = []
    for it in items:
        base = f"/learn/api/public/v1/courses/{it['calendarId']}/gradebook/columns/{it['id']}"
        fila = get(f"{base}/users/{yo}")
        intentos = get(f"{base.replace('/v1/', '/v2/', 1)}/attempts?userId={yo}")
        filas.append({
            "vence": _fecha(it["end"]),
            "curso": curso_corto(it.get("calendarName", "")),
            "titulo": it.get("title", ""),
            **estado(fila, intentos),
        })
    return sorted(filas, key=lambda f: f["vence"])


def formatear(filas: list[dict], ahora: datetime) -> list[str]:
    """Una línea por entrega, en hora de Lima."""
    lineas = []
    for f in filas:
        e = f["estado"]
        if e == "calificado":
            marca = f"✓ calificado {f['nota']:g}"
        elif e == "entregado":
            marca = f"✓ entregado {f['enviado'].astimezone(LIMA):%d/%m}"
        elif f["vence"] < ahora:
            marca = "✗ VENCIDO" + (" (solo borrador)" if e == "borrador" else "")
        else:
            marca = "· borrador sin enviar" if e == "borrador" else "· pendiente"
        vence = f["vence"].astimezone(LIMA)
        lineas.append(f"{DIAS[vence.weekday()]} {vence:%d/%m %H:%M}  {marca:<22}  {f['curso']} — {f['titulo']}")
    return lineas
