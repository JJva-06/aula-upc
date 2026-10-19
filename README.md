# aula-upc

Comando `aula` y skill de Claude Code que bajan del Aula Virtual UPC (Blackboard Ultra) el material
de tus cursos, las transcripciones de las clases grabadas y la lista de entregas pendientes, a la
carpeta de cada curso.

Usa tu propia sesión: tú inicias sesión en Chrome una vez (con MFA) y `aula` reutiliza ese perfil.
No guarda contraseñas.

## Requisitos

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Google Chrome instalado (no Chromium): `aula` abre el Chrome del sistema con un perfil propio.
- Opcional: [rclone](https://rclone.org/install/) ≥ 1.75 para `aula respaldo`.

Probado en Linux y en Windows (nativo, sin WSL). En macOS debería funcionar igual.

## Instalación

```bash
uv tool install git+https://github.com/JJva-06/aula-upc
aula login
```

`aula login` abre Chrome: entra con tu cuenta UPC y completa el MFA. La ventana detecta sola cuando
terminaste. El perfil queda en `~/.local/share/aula/profile` (en Windows,
`%LOCALAPPDATA%\aula\profile`); para usar otro, define `AULA_PROFILE_DIR`.

### En Windows

En PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # uv
uv tool install git+https://github.com/JJva-06/aula-upc
winget install Rclone.Rclone   # opcional, para `aula respaldo`
```

Abre una terminal nueva para que `aula` y `rclone` queden en el `PATH` (si `uv` avisa que
`~\.local\bin` no está en el `PATH`, corre `uv tool update-shell`).

- Los nombres de archivo del Aula con caracteres que Windows no admite (`: ? * " < > |`) se
  guardan con `_` en su lugar.
- Si una ruta pasa de 260 caracteres, ese archivo falla y queda pendiente. Usa una carpeta de
  curso corta (p. ej. `C:\cursos\1ACC0202`) o activa las rutas largas de Windows.

### Como plugin de Claude Code

Para que Claude sepa usar `aula` cuando le pidas «baja el material» o «qué entregas tengo»:

```
/plugin marketplace add JJva-06/aula-upc
/plugin install aula@aula-upc
```

## Uso

En la carpeta de un curso (idealmente un repo git):

```bash
aula init          # elige el curso de la lista y crea manifest.json
aula sync          # baja lo pendiente a materials/
aula status        # cuánto falta
```

| Comando | Qué hace |
|---|---|
| `aula check` | Dice si la sesión sigue viva |
| `aula pendientes` | Entregas de todos tus cursos por fecha, con su estado (corre desde cualquier carpeta) |
| `aula init` | Crea el `manifest.json` de un curso en el que estás matriculado |
| `aula discover` | Registra en el manifiesto el material nuevo del curso |
| `aula sync` | Baja lo pendiente a `materials/` |
| `aula status` | Resume lo bajado y lo que falta |
| `aula grabaciones` | Baja las transcripciones de las clases grabadas (`--video` incluye el .mp4) |
| `aula respaldo` | Respalda `materials/` en un remoto de rclone y, con `--liberar`, saca los videos del disco |

Si un comando dice que la sesión caducó, vuelve a correrlo: normalmente `aula` la renueva solo con
la sesión de Microsoft. Solo si eso falla hace falta `aula login` otra vez.

## Ojo

- **El material es de tus profesores.** `materials/` está en el `.gitignore`: no lo subas a un
  repo público. `manifest.json` sí se puede commitear.
- Es una herramienta personal, no oficial de la UPC. Si Blackboard cambia, puede romperse.

## Desarrollo

```bash
uv sync
uv run pytest -q
uv run ruff check .
```

La lógica vive en funciones puras y la capa de Playwright es delgada: casi todo se prueba sin
navegador.

## Licencia

MIT
