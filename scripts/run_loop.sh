#!/usr/bin/env bash
# Run the CHIA loop on a local single-machine Ray instance.
#
#   scripts/run_loop.sh --design div_unit                      dry run: every stage replays a synthetic payload
#   scripts/run_loop.sh --real --design div_unit --llm openai:gpt-5
#   scripts/run_loop.sh --real --targets configs/my_targets.yaml --design my_block \
#       --llm vertex:gemini-2.5-pro --sva-llm gemini-2.5-pro
#
# --real runs VC Formal, SVApshot, Yosys, EQY, and your LLM. Extra arguments go to chia_ppa_loop.py.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$HERE/scripts/env.sh"

PY="${CHIA_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "$HERE/.venv/bin/python" ]]; then PY="$HERE/.venv/bin/python"; else PY="python3"; fi
fi
export PYTHONUNBUFFERED=1
export SVAPSHOT_PYTHON="${SVAPSHOT_PYTHON:-$PY}"

BYPASS=("--bypass-config" "$HERE/configs/bypass.yaml")
ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--real" ]]; then BYPASS=(); else ARGS+=("$arg"); fi
done

cd "$HERE"
exec "$PY" chia_ppa_loop.py "${BYPASS[@]}" ${ARGS[@]+"${ARGS[@]}"}
