# Source this file:  source scripts/env.sh
# Puts the OSS CAD Suite and VC Formal on PATH and names the SVApshot checkout.
_chia_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export CHIA_ROOT="$_chia_root"
if [ -d "$_chia_root/toolchains/oss-cad-suite/bin" ]; then
  export PATH="$_chia_root/toolchains/oss-cad-suite/bin:$PATH"
fi
if [ -n "${VC_FORMAL_HOME:-}" ] && [ -d "$VC_FORMAL_HOME/bin" ]; then
  export PATH="$VC_FORMAL_HOME/bin:$PATH"
fi
if [ -z "${SVAPSHOT_CHECKOUT:-}" ] && [ -d "$_chia_root/external/svapshot/src/core" ]; then
  export SVAPSHOT_CHECKOUT="$_chia_root/external/svapshot"
fi
export PYTHONPATH="$_chia_root${PYTHONPATH:+:$PYTHONPATH}"
unset _chia_root
