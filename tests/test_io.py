import json

from aula.manifest import load, save


def test_load_migra_un_manifiesto_v1(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"schema_version": 1, "documents": []}))

    data = load(p)

    assert data["schema_version"] == 2


def test_save_conserva_el_orden_de_claves_y_los_acentos(tmp_path):
    p = tmp_path / "manifest.json"
    data = {"schema_version": 2, "course": {"name": "Programación Concurrente"}, "documents": []}

    save(p, data)

    texto = p.read_text()
    assert texto.index('"schema_version"') < texto.index('"course"') < texto.index('"documents"')
    assert "Programación" in texto, "no debe escapar los acentos a \\uXXXX"
    assert texto.endswith("\n")


def test_ciclo_load_save_de_un_v2_no_cambia_el_archivo(tmp_path):
    p = tmp_path / "manifest.json"
    original = json.dumps(
        {"schema_version": 2, "course": {"name": "Tópicos"}, "documents": [], "mi_seccion": {"x": 1}},
        indent=2,
        ensure_ascii=False,
    ) + "\n"
    p.write_text(original)

    save(p, load(p))

    assert p.read_text() == original
