"""Yosys EQY logic equivalence check between a gold DUT and a rewritten candidate.

The check is observational: the loop records the verdict next to the snapshot
verdict. The snapshot proves the frozen properties, while EQY compares the
outputs of both designs partition by partition on every input.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional

from . import tool_bin, tool_env
from .yosys import _prep_yosys_tree


_EQY_PASS = re.compile(r"Successfully proved designs equivalent", re.I)
_EQY_FAIL = re.compile(
    r"(?:Failed to prove equivalence|Proved inequivalence)",
    re.I,
)
_FAIL_COUNT = re.compile(
    r"Failed to prove equivalence for\s+(\d+)/(\d+)\s+partitions",
    re.I,
)
_READ_FAILED = re.compile(
    r"(?:read_gold|read_gate): job failed|Reading sources failed",
    re.I,
)
_FAIL_PART = re.compile(
    r"Failed to prove equivalence of partition\s+(\S+)",
    re.I,
)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _as_text(blob: object) -> str:
    """Decode TimeoutExpired stdout/stderr. CPython 3.10 leaves these as bytes."""
    if blob is None:
        return ""
    if isinstance(blob, bytes):
        return blob.decode("utf-8", errors="replace")
    return str(blob)


def _read_cmd(
    sources: List[str],
    top: str,
    *,
    include_dirs: Optional[List[str]],
    parameters: Optional[Dict[str, object]],
    frontend: str,
) -> str:
    quoted = " ".join(sources)
    gens = ""
    if parameters:
        gens = " " + " ".join(f"-G {key}={value}" for key, value in parameters.items())
    includes = ""
    if include_dirs:
        includes = "".join(f" -I {path}" for path in include_dirs if path)
    if frontend == "slang":
        return (
            "plugin -i slang\n"
            f"read_slang --single-unit --ignore-initial --empty-blackboxes "
            f"--top {top}{gens}{includes} {quoted}"
        )
    return f"read -sv {quoted}"


def _eqy_text(
    top: str,
    gold_read: str,
    gate_read: str,
    *,
    depth: int,
) -> str:
    return (
        "[options]\n"
        "\n"
        "[gold]\n"
        f"{gold_read}\n"
        f"prep -top {top}\n"
        "memory_map\n"
        "\n"
        "[gate]\n"
        f"{gate_read}\n"
        f"prep -top {top}\n"
        "memory_map\n"
        "\n"
        "[strategy simple]\n"
        "use sat\n"
        f"depth {depth}\n"
    )


def parse_eqy_log(text: str) -> Dict[str, object]:
    """Classify an EQY log without running the tool."""
    failed = 0
    total = 0
    match = _FAIL_COUNT.search(text)
    if match:
        failed, total = int(match.group(1)), int(match.group(2))
    failed_names = _FAIL_PART.findall(text)
    if _EQY_PASS.search(text):
        return {
            "equivalent": True,
            "inconclusive": False,
            "partitions_failed": 0,
            "partitions_total": total,
            "failed_partitions": [],
            "reason": "EQY proved the designs equivalent",
        }
    if _EQY_FAIL.search(text):
        return {
            "equivalent": False,
            "inconclusive": False,
            "partitions_failed": failed,
            "partitions_total": total,
            "failed_partitions": failed_names,
            "reason": (
                f"EQY failed equivalence for {failed}/{total} partitions"
                if failed
                else "EQY proved the designs inequivalent"
            ),
        }
    return {
        "equivalent": False,
        "inconclusive": True,
        "partitions_failed": failed,
        "partitions_total": total,
        "failed_partitions": failed_names,
        "reason": "EQY did not reach an equivalence verdict",
    }


_PASS_PARTITION = re.compile(r"Proved equivalence of partition '([^']+)'")
_COULD_NOT = re.compile(
    r"Could not prove equivalence of partition '([^']+)' using strategy '[^']*'(?::\s*(.*))?"
)
_PROVED_INEQ = re.compile(r"Proved inequivalence of partition '?([^'\s]+)'?")
_UNKNOWN_PARTITION = re.compile(r"Setting unknown status for partition '?([^'\s]+)'?")
_ERROR_PARTITION = re.compile(
    r"Execution of strategy '[^']*' on partition '([^']+)' encountered an error"
)
_DONE = re.compile(r"\bDONE \((\w+), rc=(\d+)\)")


def classify_partitions(text: str) -> Dict[str, object]:
    """Per-partition verdicts from an EQY ``logfile.txt`` or console log.

    ``fail``     a SAT counterexample shows the partitions differ
    ``unknown``  the strategy ended without a verdict (for example the depth bound)
    ``error``    the strategy process died (out of memory, solver crash)
    ``pass``     the partitions are equivalent up to the strategy's depth

    ``done`` is true once EQY printed its ``DONE`` line.
    """
    passed, failed, unknown, errors = set(), set(), set(), set()
    reasons: Dict[str, str] = {}
    for line in text.splitlines():
        m = _PASS_PARTITION.search(line)
        if m:
            passed.add(m.group(1))
            continue
        m = _COULD_NOT.search(line)
        if m:
            name, reason = m.group(1), (m.group(2) or "").strip()
            reasons[name] = reason
            (failed if "not equivalent" in reason else unknown).add(name)
            continue
        m = _PROVED_INEQ.search(line)
        if m:
            failed.add(m.group(1))
            continue
        m = _UNKNOWN_PARTITION.search(line)
        if m:
            unknown.add(m.group(1))
            continue
        m = _ERROR_PARTITION.search(line)
        if m:
            errors.add(m.group(1))
    passed -= failed | unknown
    errors -= passed | failed | unknown
    done = _DONE.search(text)
    return {
        "pass": sorted(passed),
        "fail": sorted(failed),
        "unknown": sorted(unknown),
        "error": sorted(errors),
        "reasons": reasons,
        "done": bool(done),
        "status": done.group(1) if done else "",
    }


def prepare_lec(
    gold_sources: List[str],
    gate_sources: List[str],
    top: str,
    *,
    workdir: str,
    gold_includes: Optional[List[str]] = None,
    gate_includes: Optional[List[str]] = None,
    parameters: Optional[Dict[str, object]] = None,
    depth: Optional[int] = None,
    frontend: str = "slang",
) -> str:
    """Copy both designs under ``workdir`` and write the ``.eqy`` file; return its path."""
    os.makedirs(workdir, exist_ok=True)
    depth = _int_env("CHIA_LEC_DEPTH", 10) if depth is None else depth
    gold_dir = os.path.join(workdir, "gold")
    gate_dir = os.path.join(workdir, "gate")
    shutil.rmtree(gold_dir, ignore_errors=True)
    shutil.rmtree(gate_dir, ignore_errors=True)
    gold_srcs, gold_incs = _prep_yosys_tree(gold_sources, gold_includes, gold_dir)
    gate_srcs, gate_incs = _prep_yosys_tree(gate_sources, gate_includes, gate_dir)
    config = _eqy_text(
        top,
        _read_cmd(gold_srcs, top, include_dirs=gold_incs, parameters=parameters, frontend=frontend),
        _read_cmd(gate_srcs, top, include_dirs=gate_incs, parameters=parameters, frontend=frontend),
        depth=depth,
    )
    path = os.path.join(workdir, f"{top}_{frontend}.eqy")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(config)
    return path


def execute_eqy(
    eqy_path: str,
    run_dir: str,
    *,
    jobs: int,
    resume: bool = False,
    timeout_s: Optional[int] = None,
    log_path: Optional[str] = None,
) -> Dict[str, object]:
    """Run ``eqy`` on ``eqy_path``; stream the console to ``log_path``.

    ``resume=True`` continues an existing ``run_dir`` (``eqy -c``). Returns the
    return code (124 after a timeout) and the console text.
    """
    cwd = os.path.dirname(os.path.abspath(eqy_path))
    log_path = log_path or os.path.join(cwd, "eqy_console.log")
    cmd = [
        tool_bin("eqy", "EQY"), "-c" if resume else "-f", "-d", run_dir,
        "-j", str(jobs), "--yosys", tool_bin("yosys", "YOSYS"), eqy_path,
    ]
    with open(log_path, "a" if resume else "w", encoding="utf-8") as log:
        try:
            proc = subprocess.run(
                cmd, cwd=cwd, env=tool_env(), stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, timeout=timeout_s,
            )
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = 124
            log.write(f"\nEQY timed out after {timeout_s}s\n")
    with open(log_path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    return {"returncode": code, "text": text, "log_path": log_path}


def run_lec(
    gold_sources: List[str],
    gate_sources: List[str],
    top: str,
    *,
    workdir: str,
    gold_includes: Optional[List[str]] = None,
    gate_includes: Optional[List[str]] = None,
    parameters: Optional[Dict[str, object]] = None,
    timeout_s: Optional[int] = None,
    depth: Optional[int] = None,
) -> Dict[str, object]:
    """EQY gold (baseline) vs gate (rewrite); the verdict is recorded and never gates the loop."""
    os.makedirs(workdir, exist_ok=True)
    timeout_s = _int_env("CHIA_LEC_TIMEOUT", 600) if timeout_s is None else timeout_s
    depth = _int_env("CHIA_LEC_DEPTH", 10) if depth is None else depth
    jobs = _int_env("CHIA_LEC_JOBS", 4)

    gold_dir = os.path.join(workdir, "gold")
    gate_dir = os.path.join(workdir, "gate")
    shutil.rmtree(gold_dir, ignore_errors=True)
    shutil.rmtree(gate_dir, ignore_errors=True)
    gold_srcs, gold_incs = _prep_yosys_tree(gold_sources, gold_includes, gold_dir)
    gate_srcs, gate_incs = _prep_yosys_tree(gate_sources, gate_includes, gate_dir)

    eqy_bin = tool_bin("eqy", "EQY")
    yosys_bin = tool_bin("yosys", "YOSYS")
    env = tool_env()

    last_text = ""
    last_code = 1
    frontend_used = "slang"
    for frontend in ("slang", "read_sv"):
        config = _eqy_text(
            top,
            _read_cmd(gold_srcs, top, include_dirs=gold_incs, parameters=parameters, frontend=frontend),
            _read_cmd(gate_srcs, top, include_dirs=gate_incs, parameters=parameters, frontend=frontend),
            depth=depth,
        )
        eqy_path = os.path.join(workdir, f"{top}_{frontend}.eqy")
        with open(eqy_path, "w", encoding="utf-8") as handle:
            handle.write(config)
        run_dir = os.path.join(workdir, f"run_{frontend}")
        shutil.rmtree(run_dir, ignore_errors=True)
        cmd = [eqy_bin, "-f", "-d", run_dir, "-j", str(jobs), "--yosys", yosys_bin, eqy_path]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=workdir,
                timeout=timeout_s,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            text = (_as_text(exc.stdout) + "\n" + _as_text(exc.stderr)).strip()
            text = (text + f"\nEQY timed out after {timeout_s}s\n")[-8000:]
            with open(os.path.join(workdir, "eqy_lec.log"), "w", encoding="utf-8") as handle:
                handle.write(text)
            parsed = parse_eqy_log(text)
            return {
                "ok": True,
                "equivalent": False,
                "inconclusive": True,
                "frontend": frontend,
                "returncode": 124,
                "partitions_failed": parsed["partitions_failed"],
                "partitions_total": parsed["partitions_total"],
                "failed_partitions": parsed.get("failed_partitions") or [],
                "reason": f"EQY timed out after {timeout_s}s",
                "log_tail": text[-2000:],
            }
        text = (_as_text(proc.stdout) + "\n" + _as_text(proc.stderr)).strip()
        last_text, last_code, frontend_used = text, proc.returncode, frontend
        parsed = parse_eqy_log(text)
        slang_miss = frontend == "slang" and (
            proc.returncode != 0
            and not parsed["equivalent"]
            and parsed["inconclusive"]
            and _READ_FAILED.search(text) is not None
        )
        if not slang_miss:
            break
    with open(os.path.join(workdir, "eqy_lec.log"), "w", encoding="utf-8") as handle:
        handle.write(last_text)
    parsed = parse_eqy_log(last_text)
    return {
        "ok": True,
        "equivalent": bool(parsed["equivalent"]),
        "inconclusive": bool(parsed["inconclusive"]),
        "frontend": frontend_used,
        "returncode": last_code,
        "partitions_failed": parsed["partitions_failed"],
        "partitions_total": parsed["partitions_total"],
        "failed_partitions": parsed.get("failed_partitions") or [],
        "reason": parsed["reason"],
        "log_tail": last_text[-2000:],
    }


def resume_lec(
    workdir: str,
    top: str,
    *,
    timeout_s: Optional[int] = None,
    frontend: str = "slang",
) -> Dict[str, object]:
    """Continue an existing EQY workdir (gold/gate/partition already done)."""
    timeout_s = _int_env("CHIA_LEC_TIMEOUT", 7200) if timeout_s is None else timeout_s
    jobs = _int_env("CHIA_LEC_JOBS", 8)
    eqy_path = os.path.join(workdir, f"{top}_{frontend}.eqy")
    run_dir = os.path.join(workdir, f"run_{frontend}")
    if not os.path.isfile(eqy_path):
        return {
            "ok": False,
            "equivalent": False,
            "inconclusive": True,
            "frontend": frontend,
            "returncode": 2,
            "partitions_failed": 0,
            "partitions_total": 0,
            "failed_partitions": [],
            "reason": f"missing EQY config {eqy_path}",
            "log_tail": "",
        }
    eqy_bin = tool_bin("eqy", "EQY")
    yosys_bin = tool_bin("yosys", "YOSYS")
    env = tool_env()
    cmd = [eqy_bin, "-c", "-d", run_dir, "-j", str(jobs), "--yosys", yosys_bin, eqy_path]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=workdir,
            timeout=timeout_s,
            env=env,
        )
        text = (_as_text(proc.stdout) + "\n" + _as_text(proc.stderr)).strip()
        code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        text = (_as_text(exc.stdout) + "\n" + _as_text(exc.stderr)).strip()
        text = (text + f"\nEQY timed out after {timeout_s}s\n")[-8000:]
        code = 124
    with open(os.path.join(workdir, "eqy_lec.log"), "w", encoding="utf-8") as handle:
        handle.write(text)
    parsed = parse_eqy_log(text)
    inconclusive = bool(parsed["inconclusive"]) or code == 124
    reason = parsed["reason"]
    if code == 124:
        reason = f"EQY timed out after {timeout_s}s"
        inconclusive = True
    return {
        "ok": True,
        "equivalent": bool(parsed["equivalent"]),
        "inconclusive": inconclusive,
        "frontend": frontend,
        "returncode": code,
        "partitions_failed": parsed["partitions_failed"],
        "partitions_total": parsed["partitions_total"],
        "failed_partitions": parsed.get("failed_partitions") or [],
        "reason": reason,
        "log_tail": text[-2000:],
        "resumed": True,
    }


def counterexample_traces(run_dir: str, partitions: List[str]) -> Dict[str, str]:
    """``trace.vcd`` of each failing partition that left one under ``run_dir/strategies``."""
    found: Dict[str, str] = {}
    for name in partitions:
        base = os.path.join(run_dir, "strategies", name)
        if not os.path.isdir(base):
            continue
        for strategy in sorted(os.listdir(base)):
            trace = os.path.join(base, strategy, "trace.vcd")
            if os.path.isfile(trace):
                found[name] = trace
                break
    return found


def view_waveform(vcd: str, viewer: str = "gtkwave") -> int:
    """Open a counterexample waveform in ``viewer`` (``gtkwave`` or ``surfer``) and wait."""
    return subprocess.run([tool_bin(viewer), vcd], env=tool_env()).returncode
