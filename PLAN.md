# Minecraft Hub for Linux — Plan

## Origen

Este repo es un fork personal de `Wyze3306/BedrockOnLinux` (release base 2.2.7), apuntado a `esteban-dcp/minecraft-hub-linux`. El upstream (`Wyze3306/BedrockOnLinux`) se mantiene como referencia: sincronización manual, sin auto-merge, evaluando cada release antes de traerla. Si hay que romper compatibilidad con upstream, se rompe; el fork es propio.

El proyecto original (BedrockOnLinux) instala y corre Minecraft Bedrock para Windows en Linux usando:
- `xodus-cli` (GPL-3.0) para descargar el MSIXVC desde el CDN Xbox con la cuenta MSA del usuario.
- WineGDK custom ("GDK-Proton", `Weather-OS/GDK-Proton`) como motor Wine con los stubs XSystem / XGameRuntime / XCurl que necesita Bedrock.
- Launcher Qt/PySide6 monolítico.
- Empaquetado: AppImage, .deb, .rpm, Flatpak, Nix.

El usuario posee licencias de Bedrock, Dungeons y Legends, así que las nuevas familias se pueden verificar end-to-end con `xodus-cli`.

## Objetivo

Convertir BedrockOnLinux en **Minecraft Hub for Linux**: un launcher que instale y ejecute múltiples juegos de la familia Minecraft a través de Microsoft Store, compartiendo infraestructura (descargador, motor Wine, GUI, empaquetado).

**Familias objetivo:**
- `minecraft-bedrock` (release + preview): estado actual, totalmente funcional.
- `minecraft-dungeons`: a agregar (E7).
- `minecraft-legends`: a agregar (E8), con caveat (puede no estar disponible en el Store por sunset).
- `minecraft-java`: fuera de scope de código, solo la arquitectura queda lista para sumarlo a futuro (E9).

## Roadmap por etapas

Cada etapa deja Bedrock 100% verde (la suite de tests es densa — 1400+ tests — y es el cinturón de seguridad del refactor).

### E1 — Rebranding y metadata pública
- `APP` = `"minecraft-hub"`, `PRETTY` = `"Minecraft Hub for Linux"`.
- `FLATPAK_APP_ID` = `"io.github.esteban-dcp.MinecraftHub"`.
- `SITE_URL` y `SELF_REPO` apuntan al fork.
- Renombrar el entry-point `bedrock-on-linux` → `minecraft-hub`. El viejo queda como shim de compat (`exec` al nuevo).
- `data/bedrock-on-linux.desktop`: actualizar `Name`, `Exec`, `Icon`.
- `flatpak/`: renombrar archivos `io.github.wyze3306.BedrockOnLinux.*` → `io.github.esteban-dcp.MinecraftHub.*`. Actualizar `app-id`, paths, comandos.
- `flake.nix`: `pname`, paths, ejecutable.
- `README.md`, `CHANGELOG.md`, `site/`: marca, URLs, screenshots (cuando se regeneren).
- Migración de data dir: `~/.local/share/bedrock-on-linux` debe seguir funcionando; el código de `xdg_storage`/`relocation` ya está hecho para este patrón.
- **Decisión (en revisión):** paquete Python interno sigue siendo `bol/` en E1. El rename `bol/` → `minecrafthub/` queda para E5, donde también se renombran funciones internas (`mc_version_str`, `mc_running`, etc.).

### E2 — Registry genérica de productos ✅ (parcialmente ejecutado en esta sesión)
- Reemplazar `MC_PRODUCTS` (tuple de 2 entries Bedrock) por `PRODUCTS` (tuple con `family`, `id`, `kind`, `launcher` por entry).
- `MC_PRODUCTS` queda como alias derivado para back-compat.
- Nuevos accessors en `bol/xodus.py`: `product(product_id, family=None)`, `list_products(family=None)`.
- `edition()` y `list_editions()` se mantienen con su firma, filtran por `family="minecraft-bedrock"`.
- Tests: la suite existente (test_xodus.py EditionTests, etc.) sigue verde sin cambios porque las firmas públicas no cambian.

**Estado actual:** el código está escrito y la suite pasa. Falta commit.

### E3 — Árbol de instalación y selección activa
- Hoy: `games/<edition_id>/<version>/` (Bedrock usa release/preview).
- Generalizar a `library/<family>/<id>/<version>/` o quedarse en `games/<family>/<id>/<version>/`. Migración automática desde `games/<edition>/<version>/` para usuarios Bedrock existentes.
- Reemplazar el symlink único `CONTENT` por un puntero `library/current.json` con `{family, id, version}` (o N symlinks `current.<family>`). Decide durante E3 según cuál es más limpio.
- Refactor: `bol/games.py::installed_builds()` → `library_entries()` con shape `{family, id, version, path, playable, ...}`.

### E4 — Abstracción `GameLauncher`
- Protocolo en un módulo nuevo, p.ej. `bol/launchers.py`:
  ```python
  class GameLauncher(Protocol):
      family: str
      def install(self, product, version, ...) -> Path: ...
      def launch(self, build_path, ...) -> None: ...
      def is_running(self) -> bool: ...
      def version_str(self, folder) -> str | None: ...
      def prefix_dir(self, build_path) -> Path: ...
      def wine_engine(self) -> Path | None: ...
      def gpu_profile(self) -> dict: ...
  ```
- `BedrockLauncher` envuelve todo el código actual de `launch.py` + `prefix.py` + `gamesetup.py` + `winegdk.py` + parte de `games.py`.
- Registry map: `PRODUCTS[*]["launcher"]` → módulo.
- El CLI dispatch (`play`, `setup`, `versions`) cambia para resolver `launcher` por familia antes de llamar.

### E5 — Refactor Bedrock-específicos
- `mc_version_str(folder)` → `game_version_str(folder, family)` con parser por familia (Bedrock usa la quirk `<minor><patch>` del AppxManifest; Dungeons/Legends tendrán su propio parser).
- `mc_running`/`_mc_running` (en `bol/prefix.py`) → `game_running(family)`.
- `_mc_running()` y `_config_dir()` con lógica Bedrock → mover a `launchers/bedrock.py`.
- Renombrar paquete `bol/` → `minecrafthub/`. Shim `bol.py` que re-exporta todo para no romper el entry-point legacy.
- CLI args: `--game <family>` (o `--family`); defaults que apuntan al último seleccionado.
- `bol/games.py::install_game` → `library.install(family, id, version)`.
- `installed_builds()` → `library_entries(family=None)`.

### E6 — GUI: vista "Library"
- Dividir `bol/gui.py` (167 KB, monolítica) en:
  - `gui/library.py` — vista principal con tiles por juego instalado.
  - `gui/game_detail.py` — vista por juego (edition picker, version picker, PLAY).
  - `gui/settings.py`, `gui/doctor.py`, `gui/logs.py`, `gui/profiles.py` — paneles compartidos.
- Mantener navegación con gamepad/teclado (el README lo enfatiza y los tests `test_gui_*` lo cubren).
- Tile de un juego no instalado: muestra "Install" en vez de "Play", lleva al setup.

### E7 — Dungeons end-to-end
- Investigar Store: confirmar que `xodus-cli` descarga Dungeons con el product_id correcto.
- Crear `DungeonsLauncher` (motor GDK-Proton compartido; prefijo separado en `compatdata/pfx.dungeons`).
- Parser de versión Dungeons.
- Registro CLI/GUI/CLI.
- Verificación con licencia real del usuario: instalar, lanzar, loguear MSA, jugar.

### E8 — Legends end-to-end
- **Caveat:** Legends fue sunset por Microsoft. Antes de invertir el esfuerzo:
  1. Probar `xodus-cli` con el product_id de Legends contra la cuenta del usuario.
  2. Si Microsoft sigue licenciando, adelante.
  3. Si no, dejar el slot en la registry pero marcar `launcher=None` (no jugable) y documentar en `docs/LEGENDS.md`.
- Mismo patrón que E7 si está disponible.

### E9 — Java (futuro)
- Diseño solo, sin código de runtime.
- `JavaLauncher` queda como stub que falla limpio con `"Minecraft Java support is planned but not yet implemented. Use the official Minecraft launcher or a third-party launcher like Prism/MultiMC."`.
- Documentar en `docs/JAVA.md` los puntos de integración: Mojang launcher download, JRE vendoreado, `~/.minecraft` por perfil, LWJGL stack.

### E10 — Empaquetado universal
- Un solo paquete `MinecraftHub-<ver>-x86_64.AppImage`, `minecraft-hub_<ver>_amd64.deb`, `minecraft-hub-<ver>-1.x86_64.rpm`, Flatpak `io.github.esteban-dcp.MinecraftHub`, Nix.
- Los juegos se descargan on-demand (mismo modelo que hoy).
- Ajustar identidad + dependencias (Java opcional, xodus opcional en build mínimo).

### E11 — Migración BedrockOnLinux → Minecraft Hub
- Detectar `~/.local/share/bedrock-on-linux` y migrar:
  - `game_dir` → `library/current.json`.
  - `mc_edition` → `library.current.family + .id` (con `family="minecraft-bedrock"`).
  - `mc_version` → `library.current.version`.
  - Keyring xodus: ya está en `XODUS_HOME`, no necesita migración.
  - Profiles (subdirs `BOL_HOME=...`): reescribir el path interno.
- Shims de retrocompat en `bol/` durante el primer release para que el binario `bedrock-on-linux` siga funcionando.

## Estado actual de la sesión

**E2 está ejecutado pero sin commit.** El diff toca solo:
- `bol/config.py`: introduce `PRODUCTS` con `family`/`kind`/`launcher` por entry. `MC_PRODUCTS` queda como alias derivado.
- `bol/xodus.py`: nuevos accessors `product()` y `list_products()`. `edition()` y `list_editions()` mantienen firma, filtran por `family="minecraft-bedrock"`.

**Suite actual:** 1382 passed, 9 failed, 9 skipped. Los 9 fallos son en `test_auth_errors.py` y `test_auth_settings.py`, no relacionados con E2 (son tests pre-flight de auth — hay que investigar si son pre-existentes o nuevos).

## Estructura clave del repo

| Archivo | Rol |
|---|---|
| `bol/__init__.py` | Paquete. Carga `relocation` + expone `__version__`. |
| `bol/__main__.py` | Entry point `python3 -m bol`. |
| `bol/cli.py` | argparse + dispatch a subcommands. |
| `bol/config.py` | Constantes, paths, registry de productos. |
| `bol/games.py` | Install/select/list Bedrock editions. Bedrock-específico. |
| `bol/gamesetup.py` | Crea prefijo Wine, primer launch. Bedrock. |
| `bol/launch.py` | Ejecuta Bedrock dentro del prefijo. Bedrock. |
| `bol/prefix.py` | Wine prefix management, `_mc_running()`. Bedrock. |
| `bol/winegdk.py` | Verifica + descarga el motor GDK-Proton. Bedrock. |
| `bol/xodus.py` | Descargador xodus-cli + accessors de registry. |
| `bol/auth.py` | MSA/XSTS auth. Compartido (todos los juegos GDK usan la misma auth). |
| `bol/profiles.py` | Profiles aislados por Xbox account. Compartido. |
| `bol/gpu_safety.py` | GPU/driver checks. Compartido. |
| `bol/vkd3d.py` | vkd3d-proton universal. Compartido. |
| `bol/gui.py` | GUI monolítica Qt/PySide6. |
| `bedrock-on-linux` | Entry script en raíz. Renombrar a `minecraft-hub` en E1. |
| `data/bedrock-on-linux.desktop` | Desktop file. |
| `flatpak/*.yml|*.desktop|*.metainfo.xml` | Identidad Flatpak. |
| `flake.nix` | Paquete Nix. |
| `scripts/build-*.sh` | Scripts de build por formato. |
| `tests/test_*.py` | 60+ archivos, 1400+ tests. Densidad alta, son el cinturón de seguridad. |

## Convenciones

- **No romper el upstream sync:** si un cambio toca formato de settings o paths, hay que asegurar que un `git fetch upstream && merge` no introduzca regresiones.
- **Suite de tests primero:** correr `QT_QPA_PLATFORM=offscreen pytest` antes de cada commit. Baseline conocido: 283 passed en subset reducido; 1382 passed, 9 failed, 9 skipped en suite completa.
- **Edit tool y reformateo:** el `Edit` tool reformatea bloques contiguos cuando la sustitución es grande. Para ediciones quirúrgicas en archivos con formatting estricto (como `bol/config.py`), usar un script Python que haga `read → replace exacto → write`.
- **Branching:** una rama por etapa. Merge a `main` cuando la etapa esté verde.
- **No commit sin confirmación explícita del usuario.**

## Caveats que recordar

- **Legends sunset:** validar con xodus-cli antes de invertir en E8.
- **Java fuera de scope:** dejar el `JavaLauncher` stub fallando limpio, sin código real.
- **Bedrock-only sigue siendo el camino feliz:** todas las etapas preservan la ruta Bedrock como fully working. Cualquier regresión se ve en `tests/test_xodus.py::EditionTests` y `tests/test_release_packaging.py` primero.
- **Migration en E11:** no romper installs existentes. Tests con fixtures de v2.2.7.

## Cómo continuar

1. Terminar E2: commit con mensaje `registry: introduce multi-family PRODUCTS table for the hub`.
2. Diagnosticar los 9 fallos en test_auth_errors.py y test_auth_settings.py — ver si son pre-existentes (comparar con stash del HEAD antes de E2).
3. E1: rebrand público. Editar config.py (APP/PRETTY/FLATPAK_APP_ID/SITE_URL/SELF_REPO), crear entry-point `minecraft-hub`, dejar `bedrock-on-linux` como shim, actualizar desktop/flatpak/flake/README. Verificar tests.
4. E3, E4, E5: árbol + abstracción + refactor (la carne del multi-game).
5. E6: GUI library.
6. E7: Dungeons (con verificación real del usuario).
7. E8: Legends (con caveat del Store).
8. E9: docs/Java.
9. E10: empaquetado unificado.
10. E11: migración de installs BedrockOnLinux.

## Comandos útiles

```bash
# Suite completa (necesita PySide6 + QT_QPA_PLATFORM=offscreen)
QT_QPA_PLATFORM=offscreen python3 -m pytest

# Solo xodus/games (rápido, sin Qt)
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_xodus.py tests/test_games.py tests/test_cli.py

# Verificar que el árbol del registry está bien
python3 -c "from bol.xodus import product, list_products, edition, list_editions; import json; print(json.dumps(list_products(), indent=2))"
```
