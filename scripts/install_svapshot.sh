#!/usr/bin/env bash
# Clone the SVApshot assertion generator into external/svapshot.
#
#   SVAPSHOT_REPO_URL=<git url of SVApshot> scripts/install_svapshot.sh
#   SVAPSHOT_REF=<tag or commit> SVAPSHOT_REPO_URL=... scripts/install_svapshot.sh
#
# The loop reads the checkout from external/svapshot, or from $SVAPSHOT_CHECKOUT.
# The reproduction scripts (reproduce/) read the shipped properties; SVApshot is optional for them.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${SVAPSHOT_CHECKOUT:-$ROOT/external/svapshot}"
: "${SVAPSHOT_REPO_URL:?set SVAPSHOT_REPO_URL to the SVApshot git URL}"

if [[ -d "$DEST/.git" ]]; then
  git -C "$DEST" fetch --tags --quiet
else
  mkdir -p "$(dirname "$DEST")"
  git clone --recurse-submodules "$SVAPSHOT_REPO_URL" "$DEST"
fi
if [[ -n "${SVAPSHOT_REF:-}" ]]; then
  git -C "$DEST" checkout --quiet "$SVAPSHOT_REF"
fi
test -d "$DEST/src/core" || { echo "missing src/core in $DEST" >&2; exit 1; }
echo "SVApshot checkout: $DEST ($(git -C "$DEST" rev-parse --short HEAD))"
