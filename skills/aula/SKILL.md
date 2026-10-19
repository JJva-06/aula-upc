---
name: aula
description: Úsala cuando haya que traer o revisar material del Aula Virtual UPC (Blackboard Ultra) en el repo o carpeta de un curso — "baja el material", "qué falta del aula", "sincroniza el curso", "trae las grabaciones", "la sesión del aula venció", "qué me queda pendiente", "qué entregas tengo", "ya entregué X?". Cubre el comando `aula`.
---

# `aula`: el Aula Virtual en la carpeta del curso

`aula` opera sobre la carpeta del curso **donde se lo invoca** (la que tiene `manifest.json`),
salvo `aula pendientes`, que mira todos los cursos y corre desde cualquier directorio. Se instala
global con `uv tool install git+https://github.com/JJva-06/aula-upc`. Si `aula` no existe, se le
pide al usuario que lo instale (ver el README del repo).

| Quiero | Comando |
|---|---|
| Saber si la sesión sigue viva | `aula check` |
| Entregas de **todos** los cursos, por fecha, con su estado | `aula pendientes` (`--dias 21 --atras 7`) |
| Empezar un curso nuevo | `aula init` (en la carpeta del curso) |
| Registrar material nuevo del curso en el manifiesto | `aula discover` |
| Bajar lo pendiente a `materials/` | `aula sync` |
| Ver cuánto falta bajar | `aula status` |
| Transcripciones de las clases grabadas | `aula grabaciones` (`--video` para el .mp4) |
| Respaldar `materials/` con rclone y sacar los videos del disco | `aula respaldo` (`--remoto` la primera vez, `--liberar`) |
| Sesión vencida del todo | `aula login` (abre Chrome; el login y el MFA los hace el usuario a mano) |

## Orden habitual

1. `aula check`. Si falla, casi siempre basta con volver a correr el comando: la sesión de
   Blackboard no persiste, pero la del SSO de Microsoft sí, y `aula` la renueva sola.
2. `aula discover`, después `aula sync`, después `aula status` para confirmar.
3. Si cambió `manifest.json`, eso sí se commitea; `materials/` **no**.

## Qué dice `aula pendientes`

- `✓ entregado` / `✓ calificado N`: hay un intento enviado, o ya hay nota.
- `· borrador sin enviar`: alguien del grupo abrió la entrega y no la envió. **No está entregado**,
  aunque el gradebook del Aula diga «NeedsGrading».
- `· pendiente` / `✗ VENCIDO`: no hay intento. Un «Graded» sin nota en el Aula no cuenta.

## Límites

- **Nunca** guardar credenciales ni automatizar el MFA. Si hace falta `aula login`, se le pide al
  usuario.
- El material es del profesor: no subirlo a un remoto público.
- `aula respaldo` usa rclone: si sale con código 6 por falta de rclone o del remoto, se le pide al
  usuario que lo instale (en Linux, instalador oficial, no apt; en Windows, `winget install
  Rclone.Rclone`) y corra `rclone config`. Si dice que el
  bucket es público, **no** se fuerza: se hace privado primero.
