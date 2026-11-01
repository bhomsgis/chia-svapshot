"""Wrappers around the EDA tools the loop and the reproduction scripts call.

Every module here uses the standard library only, so the reproduction
scripts run without Ray or CHIA installed.
"""

from __future__ import annotations

import os
import shutil
from typing import Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_LIBERTY = os.path.join(
    REPO_ROOT, "pdk", "sky130", "sky130_fd_sc_hd__tt_025C_1v80.lib"
)
SUITE_BIN = os.path.join(REPO_ROOT, "toolchains", "oss-cad-suite", "bin")


def tool_bin(name: str, env_var: Optional[str] = None) -> str:
    """Resolve an executable.

    Order: the environment variable (``YOSYS``, ``EQY``), the suite that
    ``scripts/install_oss_cad_suite.sh`` unpacks under ``toolchains/``, then
    ``PATH``. The bare name is returned when nothing resolves, so the caller
    reports a clear "not found" from the operating system.
    """
    if env_var:
        value = os.environ.get(env_var, "").strip()
        if value:
            return value
    bundled = os.path.join(SUITE_BIN, name)
    if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
        return bundled
    return shutil.which(name) or name


def tool_env(base: Optional[dict] = None) -> dict:
    """Environment for a subprocess that needs the suite's helper binaries.

    EQY starts ``yosys``, ``yosys-smtbmc``, and the SAT solvers by name, so the
    directory of the resolved ``eqy`` goes to the front of ``PATH``.
    """
    env = dict(os.environ if base is None else base)
    eqy = tool_bin("eqy", "EQY")
    if os.path.sep in eqy:
        env["PATH"] = os.path.dirname(os.path.abspath(eqy)) + os.pathsep + env.get("PATH", "")
    return env


def svapshot_root() -> str:
    """Checkout of the SVApshot generator.

    Order: ``$SVAPSHOT_CHECKOUT``, ``$SVAPSHOT_ROOT`` (each only when it holds
    ``src/core``), then ``external/svapshot`` from ``scripts/install_svapshot.sh``.
    """
    for key in ("SVAPSHOT_CHECKOUT", "SVAPSHOT_ROOT"):
        value = os.environ.get(key, "").strip()
        if value and os.path.isdir(os.path.join(value, "src", "core")):
            return value
    return os.path.join(REPO_ROOT, "external", "svapshot")
