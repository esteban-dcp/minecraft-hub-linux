#!/usr/bin/env bash
# Build the XCurl CA-injecting shim (src/xcurl-cashim.c) into the DLL the
# OpenSSL XCurl set ships as XCurl.dll. After building, drop the result into the
# maintainer's set and re-run scripts/package-openssl-xcurl.sh to mint a new
# OPENSSL_XCURL_REV.
#
# The shim forwards the curl_* exports to the real libcurl (xcurl_real.dll),
# injects CURLOPT_CAINFO=cacert.pem, and rewrites Minecraft's empty Xbox
# People Hub owner (/users/xuid()/ -> /users/me/) so the in-game Friends
# list/search work (peoplehub rejects the empty owner with HTTP 400).
#
# Usage: scripts/build-xcurl-shim.sh [OUT_DIR]
#   OUT_DIR defaults to the maintainer's installed set.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-$HOME/.local/share/minecraft-hub/xodus-xcurl/openssl-set}"
CC="${CC:-x86_64-w64-mingw32-gcc}"

command -v "$CC" >/dev/null || { echo "need $CC (mingw-w64)" >&2; exit 1; }
mkdir -p "$OUT_DIR"

"$CC" -O2 -shared -o "$OUT_DIR/xcurl-cashim.dll" "$SRC/src/xcurl-cashim.c"
echo "  ✓ $OUT_DIR/xcurl-cashim.dll"
echo "    (also keep the set's source in sync: cp src/xcurl-cashim.c -> set/)"
echo "Next: scripts/package-openssl-xcurl.sh  → bump OPENSSL_XCURL_REV"
