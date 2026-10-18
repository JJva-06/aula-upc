from pathlib import Path

import pytest

from aula.browser import evaluar_sesion, profile_dir


def test_profile_dir_por_defecto_vive_fuera_del_repo():
    d = profile_dir(env={})

    assert d == Path.home() / ".local/share/aula/profile"


def test_profile_dir_respeta_la_variable_de_entorno():
    d = profile_dir(env={"AULA_PROFILE_DIR": "/tmp/otro"})

    assert d == Path("/tmp/otro")


def test_solo_un_200_del_endpoint_de_identidad_cuenta_como_sesion():
    assert evaluar_sesion(200) is True


@pytest.mark.parametrize("status", [401, 403, 302, 500, 0])
def test_cualquier_otra_respuesta_es_sesion_caducada(status):
    """Regresión: antes se deducía la sesión por la forma de la URL, y la portada
    `/?new_loc=...` que sirve Blackboard sin autenticar no traía ninguna marca de
    login, así que se reportaba sesión válida con un 401 de por medio."""
    assert evaluar_sesion(status) is False


# --- Reacuñado silencioso de la sesión ------------------------------------------
# Blackboard sirve su sesión en cookies no persistentes (JSESSIONID/BbRouter), que
# Chrome no guarda en disco. Lo que sí sobrevive es la cookie del SSO de Microsoft,
# y entrar por /auth-saml/saml/login basta para reacuñar la sesión sin interacción.


def test_url_sso_apunta_al_destino_pedido():
    from aula.browser import url_sso

    u = url_sso("https://aulavirtual.upc.edu.pe/ultra/course")

    assert u.startswith("https://aulavirtual.upc.edu.pe/auth-saml/saml/login?apId=")
    assert "redirectUrl=https%3A%2F%2Faulavirtual.upc.edu.pe%2Fultra%2Fcourse" in u


def test_no_navega_si_la_sesion_ya_esta_viva():
    from aula.browser import asegurar_sesion

    navegaciones = []

    ok = asegurar_sesion(lambda: 200, lambda: navegaciones.append(1), esperar=lambda _: None)

    assert ok is True
    assert navegaciones == [], "no debe tocar el SSO si ya hay sesión"


def test_reacuna_la_sesion_pasando_por_el_sso():
    from aula.browser import asegurar_sesion

    respuestas = iter([401, 401, 200])
    navegaciones = []

    ok = asegurar_sesion(
        lambda: next(respuestas), lambda: navegaciones.append(1), esperar=lambda _: None, intentos=5
    )

    assert ok is True
    assert navegaciones == [1], "una sola pasada por el SSO"


def test_se_rinde_si_el_sso_no_reacuna():
    from aula.browser import asegurar_sesion

    ok = asegurar_sesion(lambda: 401, lambda: None, esperar=lambda _: None, intentos=3)

    assert ok is False
