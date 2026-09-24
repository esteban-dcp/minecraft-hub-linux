#!/usr/bin/env bash
# Run the checkout against the exact local engine candidate for its build rev.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REV="$(grep -m1 '^WINEGDK_BUILD_REV = ' "$SRC/bol/config.py" | cut -d'"' -f2)"
[[ -n "$REV" ]] || { echo "!! could not read WINEGDK_BUILD_REV" >&2; exit 1; }

ARCHIVE="$SRC/dist/GDK-Proton-xuser-${REV}.tar.gz"
if [[ ! -f "$ARCHIVE" ]]; then
  echo "!! engine candidate for build rev '$REV' is missing:" >&2
  echo "   $ARCHIVE" >&2
  echo "   Create it first with: scripts/package-engine.sh" >&2
  exit 1
fi

export BOL_ENGINE_ARCHIVE="$ARCHIVE"
if (( $# == 0 )); then
  set -- gui
fi

echo "== Installing exact local engine candidate: $ARCHIVE"
# A maintainer may rebuild an archive without changing REV while iterating.
# Install directly so the helper can distinguish an accepted candidate from
# ensure_winegdk() deliberately keeping an older working engine after a
# rejected update. Then wire the engine and revalidate the installed bytes
# before there is any possibility of launching the application.
PYTHONPATH="$SRC" python3 - "$REV" <<'PY'
import sys
from pathlib import Path

from bol.vkd3d import validate_engine_manifest
from bol.winegdk import (
    WINEGDK_OUT,
    _install_prebuilt_winegdk,
    ensure_winegdk,
)

expected_revision = sys.argv[1]
if not _install_prebuilt_winegdk(force=True):
    raise SystemExit(
        "local engine candidate was rejected; refusing to run with the "
        "previously installed engine")

installed = Path(ensure_winegdk(force=False))
if installed.resolve() != Path(WINEGDK_OUT).resolve():
    raise SystemExit(
        f"candidate wiring selected {installed}, expected {WINEGDK_OUT}")
try:
    manifest = validate_engine_manifest(
        WINEGDK_OUT, expected_revision, enforce_pins=True)
except Exception as exc:
    raise SystemExit(
        f"installed candidate failed final {expected_revision} validation: "
        f"{exc}") from exc
if manifest.build_rev != expected_revision:
    raise SystemExit(
        f"installed candidate is {manifest.build_rev}, "
        f"expected {expected_revision}")
print(f"Installed game engine validated: {manifest.build_rev}")
PY

echo "== Running checkout with the installed local candidate"
exec "$SRC/minecraft-hub" "$@"
