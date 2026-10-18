"""Comandos de `aula`."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

from . import (
    api,
    browser,
    cursos,
    descargas,
    grabaciones,
    manifest,
    pendientes,
    respaldo,
)

app = typer.Typer(help="Sincroniza material del Aula Virtual UPC con tu repo de curso.", no_args_is_help=True)

SIN_SESION = 2
SIN_MANIFIESTO = 3
YA_EXISTE = 4
SIN_CLASS = 5
SIN_RESPALDO = 6


def _manifiesto(ruta: Path | None) -> Path:
    p = ruta or Path.cwd() / "manifest.json"
    if not p.exists():
        typer.secho(f"No encuentro {p}. Corre `aula init` en el repo del curso.", fg="red", err=True)
        raise typer.Exit(SIN_MANIFIESTO)
    return p


@app.command()
def login(espera: int = typer.Option(600, "--espera", help="Segundos máximos de espera")):
    """Abre Chrome para que te autentiques a mano. La sesión queda guardada."""
    typer.echo(f"Perfil: {browser.profile_dir()}")
    typer.echo("Se abrirá Chrome. Tómate el tiempo que necesites: la ventana queda")
    typer.echo("abierta y detecto la sesión sola, sin que tengas que volver aquí.")
    typer.echo("")

    ultimo = {"marca": None}

    def avisar(restantes: int) -> None:
        # Un aviso por minuto, para no llenar la terminal cada 3 segundos.
        minutos = restantes // 60
        if minutos != ultimo["marca"]:
            ultimo["marca"] = minutos
            typer.echo(f"  esperando… ({minutos} min restantes)")

    if browser.login(espera_max_s=espera, al_esperar=avisar):
        typer.secho("Sesión guardada y verificada contra el endpoint de identidad.", fg="green")
    else:
        typer.secho(f"No se completó el login en {espera}s. Vuelve a intentarlo.", fg="red", err=True)
        raise typer.Exit(SIN_SESION)


@app.command()
def check():
    """Comprueba si la sesión guardada sigue viva."""
    if browser.sesion_activa():
        typer.secho("Sesión activa.", fg="green")
    else:
        typer.secho("Sesión caducada. Corre `aula login`.", fg="yellow", err=True)
        raise typer.Exit(SIN_SESION)


def _elegir_curso(matriculados: list[dict], code: str | None, course_id: str | None) -> dict:
    """El curso pedido por bandera, o el que elijas de la lista."""
    if not code and not course_id:
        typer.echo("Cursos matriculados:")
        for i, c in enumerate(matriculados, start=1):
            modalidad = f" · {c['mode']}" if c.get("mode") else ""
            typer.echo(f"  {i:>2}. {c.get('code', '?'):<10} {c.get('name', '?')}{modalidad}")
        elegido = typer.prompt("Número del curso", type=int)
        if not 1 <= elegido <= len(matriculados):
            typer.secho("Ese número no está en la lista.", fg="red", err=True)
            raise typer.Exit(SIN_MANIFIESTO)
        return matriculados[elegido - 1]

    try:
        return cursos.elegir(matriculados, code=code, course_id=course_id)
    except cursos.CursoAmbiguo:
        typer.secho(
            f"'{code}' aparece en más de un term. Desempata con --course-id.",
            fg="red",
            err=True,
        )
        raise typer.Exit(SIN_MANIFIESTO) from None
    except cursos.CursoNoEncontrado:
        typer.secho("No estás matriculado en ese curso. Córrelo sin banderas para ver la lista.", fg="red", err=True)
        raise typer.Exit(SIN_MANIFIESTO) from None


@app.command()
def init(
    code: str = typer.Option(None, "--code", help="Código del curso, p. ej. 1ACC0202"),
    course_id: str = typer.Option(None, "--course-id", help="ID de Blackboard, p. ej. _546186_1"),
    ruta: Path = typer.Option(None, "--manifest", help="Ruta al manifest.json"),
    forzar: bool = typer.Option(False, "--forzar", help="Reescribe un manifiesto que ya existe"),
    descubrir: bool = typer.Option(True, "--discover/--no-discover", help="Recorre el curso al terminar"),
):
    """Crea el manifest.json de un curso matriculado, en el directorio actual."""
    p = ruta or Path.cwd() / "manifest.json"
    if p.exists() and not forzar:
        typer.secho(f"Ya existe {p}. Usa --forzar si de verdad quieres reescribirlo.", fg="red", err=True)
        raise typer.Exit(YA_EXISTE)

    try:
        with browser.sesion_api() as cliente:
            curso = _elegir_curso(api.cursos_matriculados(cliente.get), code, course_id)

            ahora = datetime.now(UTC).isoformat(timespec="seconds")
            data = cursos.esqueleto(curso, ahora=ahora)
            manifest.save(p, data)
            typer.secho(f"{curso['name']} ({curso.get('code', '?')}) → {p}", fg="green")

            if not descubrir:
                typer.echo("Listo. Corre `aula discover` cuando quieras poblarlo.")
                return

            # Se reaprovecha la sesión ya abierta: un solo arranque de Chrome.
            documentos = api.recorrer(cliente.get, curso["blackboard_course_id"])
            data = manifest.merge(data, documentos)
            manifest.save(p, data)
    except browser.SesionCaducada:
        typer.secho("Sesión caducada. Corre `aula login`.", fg="yellow", err=True)
        raise typer.Exit(SIN_SESION) from None

    cuenta = manifest.resumen(data)
    typer.echo(f"{len(documentos)} documentos, {sum(cuenta.values())} adjuntos.")
    typer.secho("Listo: corre `aula sync`.", fg="green")


@app.command("pendientes")
def pendientes_cmd(
    dias: int = typer.Option(21, "--dias", help="Cuántos días hacia adelante mirar"),
    atras: int = typer.Option(7, "--atras", help="Cuántos días hacia atrás, para ver lo que venció"),
):
    """Lista las entregas de todos los cursos, por fecha, con su estado."""
    ahora = datetime.now(UTC)
    try:
        with browser.sesion_api() as cliente:
            filas = pendientes.listar(cliente.get, ahora - timedelta(days=atras), ahora + timedelta(days=dias))
    except browser.SesionCaducada:
        typer.secho("Sesión caducada. Corre `aula login`.", fg="yellow", err=True)
        raise typer.Exit(SIN_SESION) from None

    if not filas:
        typer.echo("Sin entregas en la ventana.")
        return
    for linea in pendientes.formatear(filas, ahora):
        typer.echo(linea)


@app.command()
def status(ruta: Path = typer.Option(None, "--manifest", help="Ruta al manifest.json")):
    """Muestra cuántos adjuntos están descargados y cuántos faltan."""
    data = manifest.load(_manifiesto(ruta))
    curso = data.get("course", {})
    typer.echo(f"{curso.get('name', '(curso sin nombre)')} — {curso.get('code', '?')}")
    typer.echo(f"schema v{data.get('schema_version')} · {len(data.get('documents', []))} documentos")

    cuenta = manifest.resumen(data)
    if not cuenta:
        typer.echo("Sin adjuntos registrados.")
    for estado in sorted(cuenta):
        typer.echo(f"  {estado:<12} {cuenta[estado]}")

    if remoto := (data.get("backup") or {}).get("remote"):
        cuenta_respaldo = respaldo.resumen(data)
        typer.echo(f"Respaldo ({remoto}): {cuenta_respaldo['respaldados']} respaldados · "
                   f"{cuenta_respaldo['liberados']} liberados del disco")


def _id_de_curso(data: dict) -> str:
    cid = data.get("course", {}).get("blackboard_course_id")
    if not cid:
        typer.secho(
            "El manifiesto no tiene `course.blackboard_course_id`. "
            "Sácalo de la URL del curso en Aula Virtual (/ultra/courses/<ID>/outline).",
            fg="red",
            err=True,
        )
        raise typer.Exit(SIN_MANIFIESTO)
    return cid


@app.command()
def discover(ruta: Path = typer.Option(None, "--manifest", help="Ruta al manifest.json")):
    """Recorre el curso y añade al manifiesto lo que aún no estaba."""
    p = _manifiesto(ruta)
    data = manifest.load(p)
    cid = _id_de_curso(data)

    antes = manifest.resumen(data)
    try:
        with browser.sesion_api() as cliente:
            documentos = api.recorrer(cliente.get, cid)
    except browser.SesionCaducada:
        typer.secho("Sesión caducada. Corre `aula login`.", fg="yellow", err=True)
        raise typer.Exit(SIN_SESION) from None

    typer.echo(f"{len(documentos)} documentos con contenido en el curso.")
    nueva = manifest.merge(data, documentos)
    manifest.save(p, nueva)

    despues = manifest.resumen(nueva)
    añadidos = sum(despues.values()) - sum(antes.values())
    typer.secho(f"Manifiesto actualizado: {añadidos} adjuntos nuevos.", fg="green")
    for estado in sorted(despues):
        typer.echo(f"  {estado:<12} {despues[estado]}")


@app.command()
def sync(ruta: Path = typer.Option(None, "--manifest", help="Ruta al manifest.json")):
    """Baja a materials/ todo lo que esté pendiente."""
    p = _manifiesto(ruta)
    raiz = p.parent
    data = manifest.load(p)
    cid = _id_de_curso(data)

    try:
        with browser.sesion_api() as cliente:
            # Se redescubre primero: las URLs de bbcswebdav van firmadas y caducan,
            # así que las del manifiesto pueden estar rancias.
            data = manifest.merge(data, api.recorrer(cliente.get, cid))
            # Y antes de bajar nada, cada adjunto se queda con una ruta propia.
            data = descargas.asignar_destinos(data)
            bajados, fallidos, saltados = _bajar_todo(cliente, data, raiz)
    except browser.SesionCaducada:
        typer.secho("Sesión caducada. Corre `aula login`.", fg="yellow", err=True)
        raise typer.Exit(SIN_SESION) from None
    finally:
        manifest.save(p, data)

    typer.echo("")
    typer.secho(f"{bajados} descargados, {saltados} ya estaban, {fallidos} fallaron.", fg="green")


def _bajar_todo(cliente, data: dict, raiz: Path) -> tuple[int, int, int]:
    """Descarga lo pendiente. Un archivo que falla no aborta el resto.

    Cuenta con que `descargas.asignar_destinos` ya le dio su ruta a cada adjunto.
    """
    bajados = fallidos = saltados = 0

    for doc in data.get("documents", []):
        for att in doc.get("attachments", []):
            if not descargas.necesita_descarga(att, raiz):
                saltados += 1
                continue

            destino = att["local_path"]
            try:
                contenido = cliente.bytes(att["url"])
            except Exception as e:  # noqa: BLE001 — cualquier fallo se anota y se sigue
                att["status"] = "unavailable"
                att["reason"] = f"{type(e).__name__}: {e}"[:200]
                typer.secho(f"  ✗ {att['name']}: {att['reason']}", fg="red")
                fallidos += 1
                continue

            archivo = raiz / destino
            archivo.parent.mkdir(parents=True, exist_ok=True)
            archivo.write_bytes(contenido)

            att.update({
                "status": "downloaded",
                "size_bytes": len(contenido),
                "sha256": hashlib.sha256(contenido).hexdigest(),
                "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            })
            att.pop("reason", None)
            typer.echo(f"  ✓ {destino}  ({len(contenido):,} bytes)")
            bajados += 1

    return bajados, fallidos, saltados


def _duracion(segundos: int | None) -> str:
    s = segundos or 0
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


@app.command("grabaciones")
def grabaciones_cmd(
    ruta: Path = typer.Option(None, "--manifest", help="Ruta al manifest.json"),
    video: bool = typer.Option(False, "--video", help="Incluye el .mp4 del locutor activo (cientos de MB)"),
    pantalla: bool = typer.Option(False, "--pantalla", help="Incluye el .mp4 de la pantalla compartida"),
    listar: bool = typer.Option(False, "--listar", help="Solo lista; no baja nada ni toca el manifiesto"),
):
    """Baja las transcripciones de las grabaciones de clase a materials/grabaciones/."""
    p = _manifiesto(ruta)
    raiz = p.parent
    data = manifest.load(p)
    cid = _id_de_curso(data)
    tipos = grabaciones.tipos_pedidos(video=video, pantalla=pantalla)

    cuenta = (0, 0, 0)
    try:
        with browser.sesion_class(cid) as clase:
            school = clase.school_uuid()
            lista = grabaciones.parsear_lista(clase.get(grabaciones.ruta_lista(school, clase.class_uuid)))
            typer.echo(f"{len(lista)} grabaciones disponibles en Class.")
            if listar:
                nombres = grabaciones.nombres_base(lista)
                for g in lista:
                    typer.echo(f"  {nombres[g['recording_id']]}  {_duracion(g['duration_s'])}")
                return

            data = grabaciones.merge(data, grabaciones.registros(lista, tipos))
            cuenta = _bajar_grabaciones(clase, school, data, raiz, tipos)
    except browser.SesionCaducada:
        typer.secho("Sesión caducada. Corre `aula login`.", fg="yellow", err=True)
        raise typer.Exit(SIN_SESION) from None
    except browser.SinClass as e:
        typer.secho(f"Sin grabaciones: {e}.", fg="yellow", err=True)
        raise typer.Exit(SIN_CLASS) from None
    except grabaciones.ListaNoDisponible:
        # Un curso sin grabaciones responde con una lista vacía; si no hubo respuesta
        # es que Class rechazó la petición, y callarlo parecería un curso vacío.
        typer.secho("Class no devolvió el listado de grabaciones. Reintenta; si sigue, corre `aula login`.", fg="red", err=True)
        raise typer.Exit(SIN_CLASS) from None
    finally:
        if not listar:
            manifest.save(p, data)

    bajados, fallidos, saltados = cuenta
    typer.echo("")
    typer.secho(f"{bajados} descargados, {saltados} ya estaban, {fallidos} fallaron.", fg="green")


def _bajar_grabaciones(clase, school: str, data: dict, raiz: Path, tipos: list[str]) -> tuple[int, int, int]:
    """Baja los artefactos pedidos que falten. Un fallo no aborta el resto y se reintenta
    en la próxima corrida: las URLs firmadas se piden de nuevo cada vez."""
    bajados = fallidos = saltados = 0

    for rec in data.get(grabaciones.CLAVE_MANIFIESTO, []):
        pedidos = [a for a in rec.get("artifacts", []) if a.get("kind") in tipos]
        faltan = [a for a in grabaciones.pendientes({"artifacts": pedidos}, raiz)]
        saltados += len(pedidos) - len(faltan)

        if faltan:
            rid = rec["recording_id"]
            hay = grabaciones.disponibles(clase.get(grabaciones.ruta_detalle(school, clase.class_uuid, rid)))
            for art in faltan:
                url = None
                if art["kind"] in hay:
                    respuesta = clase.get(grabaciones.ruta_artefactos(school, clase.class_uuid, rid, art["kind"]))
                    url = grabaciones.url_de_artefacto(respuesta, art["kind"])
                if not url:
                    typer.secho(f"  · {art['name']}: Class aún no lo ofrece", fg="yellow")
                    fallidos += 1
                    continue
                try:
                    tamano, sha = descargas.guardar_flujo(browser.abrir_url, url, raiz / art["local_path"])
                except Exception as e:  # noqa: BLE001 — se anota y se sigue; queda pendiente
                    art["reason"] = f"{type(e).__name__}: {e}"[:200]
                    typer.secho(f"  ✗ {art['name']}: {art['reason']}", fg="red")
                    fallidos += 1
                    continue
                art.update({
                    "status": "downloaded",
                    "size_bytes": tamano,
                    "sha256": sha,
                    "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                })
                art.pop("reason", None)
                typer.echo(f"  ✓ {art['local_path']}  ({tamano:,} bytes)")
                bajados += 1

        _escribir_texto(rec, raiz)

    return bajados, fallidos, saltados


def _escribir_texto(rec: dict, raiz: Path) -> None:
    """Deja junto al .vtt de la transcripción su versión en prosa, si aún no está."""
    for art in rec.get("artifacts", []):
        if art.get("kind") != "audio_transcript" or art.get("status") != "downloaded":
            continue
        vtt = raiz / art["local_path"]
        txt = vtt.with_suffix(".txt")
        if vtt.exists() and not txt.exists():
            txt.write_text(grabaciones.vtt_a_texto(vtt.read_text(encoding="utf-8")), encoding="utf-8")
            typer.echo(f"  ✓ {txt.relative_to(raiz)}")


def _mb(n: int) -> str:
    return f"{n / 1_000_000:,.1f} MB"


@app.command("respaldo")
def respaldo_cmd(
    ruta: Path = typer.Option(None, "--manifest", help="Ruta al manifest.json"),
    remoto: str = typer.Option(None, "--remoto", help="Remoto de rclone y carpeta base, p. ej. hf:aula u onedrive:aula"),
    liberar: bool = typer.Option(False, "--liberar", help="Borra del disco los .mp4 que el remoto tiene idénticos"),
):
    """Respalda materials/ en un remoto de rclone (un bucket privado de HF, OneDrive…)."""
    p = _manifiesto(ruta)
    raiz = p.parent
    data = manifest.load(p)

    registrado = (data.get("backup") or {}).get("remote")
    if remoto and registrado and not registrado.startswith(remoto):
        typer.secho(f"El manifiesto ya fija el destino en {registrado}; se ignora --remoto. "
                    "Para moverlo, edita `backup.remote` en el manifiesto.", fg="yellow", err=True)
    try:
        dest = respaldo.destino(data, remoto or respaldo.remoto_base())
    except respaldo.FaltaRemoto as e:
        typer.secho(f"No sé a dónde subirlo: {e}.", fg="red", err=True)
        raise typer.Exit(SIN_RESPALDO) from None
    except respaldo.SinDestino as e:
        typer.secho(f"No sé a qué carpeta subirlo: {e}.", fg="red", err=True)
        raise typer.Exit(SIN_MANIFIESTO) from None

    bucket = None
    try:
        tipo = respaldo.comprobar_remoto(respaldo.correr, dest)
        bucket = respaldo.comprobar_privado(respaldo.correr, respaldo.sondear, dest, tipo)
        if bucket:
            typer.echo(f"Bucket de HF {bucket}: no se ve sin token.")
        elif tipo == "s3":
            typer.secho("El remoto es S3 pero no un bucket de HF: no puedo comprobar que sea privado.",
                        fg="yellow", err=True)

        falta = respaldo.bytes_por_subir(data, raiz)
        libre = respaldo.espacio_libre(respaldo.correr, dest)
        if libre is not None and falta > libre:
            raise respaldo.RcloneFallo(f"No cabe: hay {_mb(falta)} por subir y en el remoto quedan {_mb(libre)}.")

        excluidos = respaldo.alterados(data, raiz)
        for alterado in excluidos:
            typer.secho(f"  ✗ {alterado}: cambió en disco después de respaldarse y ya no es la versión del "
                        "manifiesto; no se sube (la copia buena sigue en el remoto)", fg="red")

        typer.echo(f"materials/ → {dest}/materials  ({_mb(falta)} nuevos según el manifiesto)")
        sello = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        respaldo.subir(respaldo.correr, raiz, dest, excluidos, sello)
        ahora = datetime.now(UTC).isoformat(timespec="seconds")
        data = respaldo.marcar_subidos(data, raiz, dest, ahora=ahora)
        # Se guarda ya: si luego falla la liberación, lo subido queda registrado.
        manifest.save(p, data)

        if liberar:
            data = _liberar(p, data, raiz, dest, sello)
    except (respaldo.RcloneNoDisponible, respaldo.RcloneFallo) as e:
        mensaje = str(e)
        mensaje = mensaje[:1].upper() + mensaje[1:]
        if bucket and not isinstance(e, respaldo.BucketPublico):
            mensaje += f" Si el bucket no existe, créalo privado: `hf buckets create {bucket} --private`."
        typer.secho(mensaje, fg="red", err=True)
        raise typer.Exit(SIN_RESPALDO) from None

    cuenta_respaldo = respaldo.resumen(data)
    typer.secho(f"Listo: {cuenta_respaldo['respaldados']} archivos respaldados, "
                f"{cuenta_respaldo['liberados']} solo en el remoto.", fg="green")


def _liberar(p: Path, data: dict, raiz: Path, dest: str, sello: str) -> dict:
    if not respaldo.liberables(data):
        typer.echo("Ningún video por liberar.")
        return data
    r = respaldo.liberar_verificados(respaldo.correr, data, raiz, dest, sello)
    manifest.save(p, r.data)
    for ruta in r.liberados:
        typer.echo(f"  ↑ {ruta}  (solo en el remoto)")
    for ruta in r.corruptos:
        typer.secho(f"  ✗ {ruta}: en disco no es la versión del manifiesto; no se toca. `aula grabaciones` "
                    f"lo vuelve a bajar; la versión buena está en {dest} (en materials/ o, si se pisó, "
                    "en _reemplazados/)", fg="red")
    for ruta in r.sin_confirmar:
        typer.secho(f"  ✗ {ruta}: el remoto no lo tiene idéntico ni tras resubirlo; sigue en disco", fg="red")
    typer.secho(f"Liberados: {len(r.liberados)} · {_mb(r.bytes_liberados)} menos en disco.", fg="green")
    return r.data
