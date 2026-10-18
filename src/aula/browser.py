"""Capa de navegador: perfil persistente y estado de sesión.

Deliberadamente fina. La lógica que merece tests vive arriba como funciones puras;
abrir el navegador se verifica a mano.

El helper NUNCA guarda credenciales ni automatiza el MFA: reutiliza una sesión que
abrió una persona con `aula login`.
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

BASE_URL = "https://aulavirtual.upc.edu.pe"

# Verdad de terreno sobre la autenticación. Responde 200 con la identidad si hay
# sesión y 401 si no.
#
# No se deduce la sesión mirando la URL: sin autenticar, Blackboard sirve su propia
# portada en `/?new_loc=...`, que no se distingue de una página normal por su forma.
# Los estados no autenticados no son enumerables; el endpoint sí es un sí o un no.
ENDPOINT_IDENTIDAD = "/learn/api/public/v1/users/me"


class SesionCaducada(RuntimeError):
    """No hay sesión y el SSO no pudo reacuñarla en silencio."""

# Punto de entrada del SSO de UPC, tomado del enlace "SAML Docentes y Alumnos" de
# la portada de Blackboard.
#
# Blackboard entrega su sesión en cookies NO persistentes (JSESSIONID, BbRouter):
# Chrome no las escribe a disco, así que un perfil guardado nunca conserva la sesión.
# Lo que sí persiste es la cookie del SSO de Microsoft (ESTSAUTHPERSISTENT), y pasar
# por esta URL basta para reacuñar la sesión de Blackboard sin ninguna interacción.
SSO_AP_ID = "_4893_1"


def url_sso(destino: str) -> str:
    """URL del SSO que, tras autenticar, deja al navegador en `destino`."""
    from urllib.parse import quote

    return f"{BASE_URL}/auth-saml/saml/login?apId={SSO_AP_ID}&redirectUrl={quote(destino, safe='')}"


def asegurar_sesion(obtener_status, navegar_sso, esperar, intentos: int = 8) -> bool:
    """Garantiza sesión viva, reacuñándola por el SSO si hace falta.

    Recibe sus colaboradores como parámetros para poder probar el flujo sin navegador.
    """
    if evaluar_sesion(obtener_status()):
        return True

    navegar_sso()
    for _ in range(intentos):
        if evaluar_sesion(obtener_status()):
            return True
        esperar(2.5)
    return False


def profile_dir(env: dict | None = None) -> Path:
    """Dónde vive el perfil de Chrome dedicado. Fuera de cualquier repo."""
    env = os.environ if env is None else env
    if override := env.get("AULA_PROFILE_DIR"):
        return Path(override)
    return Path.home() / ".local/share/aula/profile"


def evaluar_sesion(status: int) -> bool:
    """Traduce la respuesta del endpoint de identidad a un veredicto."""
    return status == 200


# --- Capa fina sobre Playwright -------------------------------------------------
# Sin tests automáticos a propósito: abrir un navegador contra el sitio en vivo de
# la universidad no es algo que deba correr en una suite.


def _contexto(headless: bool):
    """Abre Chrome con el perfil dedicado. Usa el Chrome del sistema, no baja Chromium."""
    from playwright.sync_api import sync_playwright

    destino = profile_dir()
    destino.mkdir(parents=True, exist_ok=True)
    destino.chmod(0o700)

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(destino),
        channel="chrome",
        headless=headless,
        accept_downloads=True,
    )
    return pw, ctx


def _status_identidad(ctx) -> int:
    """Consulta el endpoint de identidad reutilizando las cookies del contexto."""
    try:
        return ctx.request.get(BASE_URL + ENDPOINT_IDENTIDAD, timeout=20_000).status
    except Exception:  # noqa: BLE001 — red caída, DNS, timeout: todo significa "sin sesión"
        return 0


def sesion_activa() -> bool:
    """¿Hay sesión utilizable? Intenta reacuñarla en silencio. No abre ventana."""
    if not profile_dir().exists():
        return False
    pw, ctx = _contexto(headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        return asegurar_sesion(
            obtener_status=lambda: _status_identidad(ctx),
            navegar_sso=lambda: page.goto(
                url_sso(f"{BASE_URL}/ultra/course"), wait_until="domcontentloaded", timeout=60_000
            ),
            esperar=lambda s: page.wait_for_timeout(int(s * 1000)),
        )
    finally:
        ctx.close()
        pw.stop()


def login(espera_max_s: int = 300, al_esperar=None) -> bool:
    """Abre una ventana para que el usuario se autentique a mano.

    Sondea el endpoint de identidad hasta que responda 200. La ventana permanece
    abierta todo ese tiempo: el ritmo lo marca la persona, no un `wait_until`.
    """
    pw, ctx = _contexto(headless=False)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # Directo al SSO: si la cookie de Microsoft sigue viva, esto se resuelve solo
        # en segundos y el usuario no tiene que escribir nada.
        page.goto(url_sso(f"{BASE_URL}/ultra/course"), wait_until="domcontentloaded")

        limite = time.time() + espera_max_s
        while time.time() < limite:
            if evaluar_sesion(_status_identidad(ctx)):
                return True
            if al_esperar:
                al_esperar(int(limite - time.time()))
            page.wait_for_timeout(3000)
        return False
    finally:
        ctx.close()
        pw.stop()


@contextmanager
def sesion_api():
    """Abre el navegador, garantiza sesión y cede un `get(path) -> dict | None`.

    Todo comando que hable con la API entra por aquí: así la reautenticación
    silenciosa ocurre siempre y en un solo sitio.
    """
    pw, ctx = _contexto(headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        viva = asegurar_sesion(
            obtener_status=lambda: _status_identidad(ctx),
            navegar_sso=lambda: page.goto(
                url_sso(f"{BASE_URL}/ultra/course"), wait_until="domcontentloaded", timeout=60_000
            ),
            esperar=lambda s: page.wait_for_timeout(int(s * 1000)),
        )
        if not viva:
            raise SesionCaducada

        yield _Cliente(ctx)
    finally:
        ctx.close()
        pw.stop()


class _Cliente:
    """Acceso a la API y a los archivos, reutilizando la sesión del navegador."""

    def __init__(self, ctx) -> None:
        self._ctx = ctx

    def get(self, path: str) -> dict | None:
        """JSON de un endpoint de la API, o None si no respondió 200."""
        r = self._ctx.request.get(BASE_URL + path, timeout=30_000)
        return json.loads(r.text()) if r.status == 200 else None

    def bytes(self, url: str) -> bytes:
        """Contenido de un archivo. Lanza si el servidor no lo entrega."""
        r = self._ctx.request.get(url, timeout=120_000)
        if r.status != 200:
            raise OSError(f"HTTP {r.status}")
        return r.body()


# --- Grabaciones: Class por LTI ------------------------------------------------------
# Las grabaciones no están en la API de Blackboard sino en Class (Class Technologies),
# que Ultra lanza por LTI 1.3 desde el panel «Launch Class» del curso. El lanzamiento
# (OIDC + id_token firmado por Blackboard) solo lo puede hacer el navegador con la
# sesión viva, así que se hace igual que una persona: clic en «Launch Class».
#
# Tras el lanzamiento, la SPA de Class (upc.class.com) llama a su API REST enviando
# la cabecera `x-class-user-uuid`; no usa cookies ni `Authorization`. Aquí no se
# fabrica esa identidad: se toma la que la propia SPA recibió del LTI y las llamadas
# se hacen con `fetch` desde dentro de su iframe, como las hace ella.

CABECERA_USUARIO_CLASS = "x-class-user-uuid"
_API_CLASS = re.compile(r"^https://[a-z0-9-]+\.rest\.[a-z0-9.-]+\.class\.com")

_FETCH_JSON = """async ([url, cabeceras]) => {
    const r = await fetch(url, {headers: cabeceras});
    return [r.status, await r.text()];
}"""


class SinClass(RuntimeError):
    """El curso no ofrece «Launch Class», o Class no terminó de abrir."""


@contextmanager
def sesion_class(course_id: str, espera_s: int = 60):
    """Abre el navegador, lanza Class en el curso y cede un cliente de su API."""
    from .grabaciones import class_uuid_de_url

    pw, ctx = _contexto(headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        viva = asegurar_sesion(
            obtener_status=lambda: _status_identidad(ctx),
            navegar_sso=lambda: page.goto(
                url_sso(f"{BASE_URL}/ultra/course"), wait_until="domcontentloaded", timeout=60_000
            ),
            esperar=lambda s: page.wait_for_timeout(int(s * 1000)),
        )
        if not viva:
            raise SesionCaducada

        capturado: dict[str, str] = {}

        def al_pedir(req) -> None:
            if "uuid" in capturado or not (m := _API_CLASS.match(req.url)):
                return
            try:
                cabeceras = req.all_headers()
            except Exception:  # noqa: BLE001 — la petición pudo cancelarse; llegará otra
                return
            if uuid := cabeceras.get(CABECERA_USUARIO_CLASS):
                capturado.update(base=m.group(0), uuid=uuid)

        ctx.on("request", al_pedir)
        page.goto(f"{BASE_URL}/ultra/courses/{course_id}/outline", wait_until="domcontentloaded", timeout=60_000)

        # El panel «Detalles y acciones» carga tarde; el texto del botón no se traduce.
        boton = page.get_by_text("Launch Class")
        for _ in range(20):
            if boton.count():
                break
            page.wait_for_timeout(1000)
        else:
            raise SinClass("el curso no ofrece «Launch Class»")
        boton.first.click()

        for _ in range(espera_s):
            page.wait_for_timeout(1000)
            marco = next((f for f in page.frames if class_uuid_de_url(f.url)), None)
            if marco and "uuid" in capturado:
                break
        else:
            raise SinClass("Class no terminó de abrir")

        yield _ClienteClass(marco, capturado["base"], capturado["uuid"], class_uuid_de_url(marco.url))
    finally:
        ctx.close()
        pw.stop()


class _ClienteClass:
    """La API de Class, llamada desde el iframe de la SPA con la identidad del LTI."""

    def __init__(self, marco, base: str, uuid_usuario: str, class_uuid: str) -> None:
        self._marco = marco
        self._base = base
        self._cabeceras = {"user_uuid": uuid_usuario, CABECERA_USUARIO_CLASS: uuid_usuario}
        self.class_uuid = class_uuid

    def get(self, path: str) -> dict | None:
        """JSON de un endpoint de Class, o None si no respondió 200."""
        status, texto = self._marco.evaluate(_FETCH_JSON, [self._base + path, self._cabeceras])
        return json.loads(texto) if status == 200 else None

    def school_uuid(self) -> str:
        info = self.get("/api/zoom/get_school_info") or {}
        if not info.get("school_uuid"):
            raise SinClass("Class no devolvió el uuid de la institución")
        return info["school_uuid"]


def abrir_url(url: str):
    """Abre una URL firmada de CloudFront. No lleva cookies: la firma es la autorización."""
    import urllib.request

    return urllib.request.urlopen(url, timeout=120)
