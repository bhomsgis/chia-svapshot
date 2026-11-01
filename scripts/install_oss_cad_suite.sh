#!/usr/bin/env bash
# Download the OSS CAD Suite (Yosys, yosys-slang, EQY, SAT solvers) into toolchains/.
#
#   scripts/install_oss_cad_suite.sh              # release 2026-08-31 (Yosys 0.68, EQY 0.68)
#   OSS_CAD_TAG=2026-08-31 scripts/install_oss_cad_suite.sh
#
# The reproduction scripts find toolchains/oss-cad-suite/bin automatically.
# Set YOSYS=/path/to/yosys and EQY=/path/to/eqy to use another install.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${OSS_CAD_TAG:-2026-08-31}"
STAMP="${TAG//-/}"
case "$(uname -m)" in
  x86_64)  ARCH=x64 ;;
  aarch64) ARCH=arm64 ;;
  *) echo "unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac
URL="https://github.com/YosysHQ/oss-cad-suite-build/releases/download/${TAG}/oss-cad-suite-linux-${ARCH}-${STAMP}.tgz"
DEST="$ROOT/toolchains"

if [[ -x "$DEST/oss-cad-suite/bin/yosys" ]]; then
  echo "already installed: $DEST/oss-cad-suite"
else
  mkdir -p "$DEST"
  archive="$DEST/oss-cad-suite-${STAMP}.tgz"
  echo "downloading $URL"
  if command -v curl >/dev/null 2>&1; then
    curl -L --fail --retry 3 -o "$archive" "$URL"
  else
    wget -O "$archive" "$URL"
  fi
  tar -xzf "$archive" -C "$DEST"
  rm -f "$archive"
fi

"$DEST/oss-cad-suite/bin/yosys" -V
"$DEST/oss-cad-suite/bin/eqy" --version 2>/dev/null || true
