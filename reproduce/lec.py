#!/usr/bin/env python3
"""Reproduce the logic equivalence checks with Yosys EQY.

Each case compares the original RTL (gold) with the optimised RTL (gate), or with
the RTL that ``--rtl`` selects. EQY matches the two designs by name, splits them into
partitions (one per output port, register, and memory bit), and runs a SAT strategy with
a bounded depth on every partition (``[strategy simple] use sat; depth 10``). A
partition ends as

``PASS``     equivalent up to the depth bound,
``FAIL``     a SAT counterexample shows the two designs differ,
``UNKNOWN``  the strategy ended with an unknown result.

The shipped numbers list the FAIL and UNKNOWN partitions of each case. A
failing partition marks a place where the rewrite changes behaviour that the
retained SVApshot properties admit (a schedule change inside the module or an
output change outside every property).

Usage::

    python3 reproduce/lec.py                  # three cases, original vs optimised
    python3 reproduce/lec.py tlb --jobs 8
    python3 reproduce/lec.py ptw --timeout 7200
    python3 reproduce/lec.py ptw --rtl original          # identity check: original vs original
    python3 reproduce/lec.py tlb --rtl-dir ~/my_rtl/tlb  # your RTL against the original
    python3 reproduce/lec.py tlb --gui                   # GTKWave on a failing partition's trace
    python3 reproduce/lec.py --analyse-only   # re-read earlier results/lec runs

Runtime is dominated by the partition step (about 7 minutes for tlb before the SAT
jobs start). A killed solver process (memory pressure) triggers up to ``--retries``
resumed runs with half the jobs.

``--gui`` opens the counterexample waveform (``trace.vcd``) of a FAIL partition in
GTKWave (``--viewer surfer`` for Surfer). ``--partition NAME`` picks the partition.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reproduce.common import (  # noqa: E402
    Table,
    add_rtl_arguments,
    case_parser,
    load_cases,
    load_expected,
    pick_cases,
    relative,
    rtl_sources,
    select_rtl,
    write_json,
)
from chia_svapshot.tools.eqy import (  # noqa: E402
    classify_partitions,
    counterexample_traces,
    execute_eqy,
    prepare_lec,
    view_waveform,
)
from chia_svapshot.tools import tool_bin  # noqa: E402

EXIT_INCOMPLETE = 4


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def verdict(row: Dict[str, Any]) -> str:
    if not row["done"]:
        return "incomplete"
    if row["fail"]:
        return "inequivalent"
    if row["unknown"] or row["error"]:
        return "inconclusive"
    return "equivalent"


def case_dir(results: str, case: str, which: str) -> str:
    """Working folder of one check; the shipped comparison keeps the plain case name."""
    return os.path.join(results, "lec", case if which == "optimised" else f"{case}_{which}")


def run_case(case: str, spec: Dict[str, Any], args, selection, which: str) -> Dict[str, Any]:
    workdir = case_dir(args.results, case, which)
    top = spec["top"]
    run_dir = os.path.join(workdir, "run")
    eqy_path = os.path.join(workdir, f"{top}_slang.eqy")
    log_path = os.path.join(workdir, "eqy_console.log")
    started = time.time()
    if not args.analyse_only:
        eqy_path = prepare_lec(
            rtl_sources(case, "original", spec),
            selection.sources(case, spec, which),
            top, workdir=workdir, depth=args.depth,
        )
        jobs = args.jobs
        for attempt in range(args.retries + 1):
            resume = attempt > 0
            print(f"[{case}] EQY {'resume' if resume else 'start'} (jobs={jobs}) ...", flush=True)
            out = execute_eqy(
                eqy_path, run_dir, jobs=jobs, resume=resume,
                timeout_s=args.timeout, log_path=log_path,
            )
            state = classify_partitions(_read(os.path.join(run_dir, "logfile.txt")) + "\n" + out["text"])
            if state["done"] or out["returncode"] == 124:
                break
            jobs = max(1, jobs // 2)
            print(f"[{case}] EQY ended before its DONE line ({len(state['error'])} errored partitions); retrying", flush=True)
    text = _read(os.path.join(run_dir, "logfile.txt")) + "\n" + _read(log_path)
    row = classify_partitions(text)
    strategies = os.path.join(run_dir, "strategies")
    total = len(os.listdir(strategies)) if os.path.isdir(strategies) else 0
    row["total"] = total
    row["elapsed_s"] = round(time.time() - started, 1)
    row["verdict"] = verdict(row)
    row["pass_count"] = len(row["pass"])
    row["pass"] = []
    return row


def compare(case: str, row: Dict[str, Any], expected: Dict[str, Any], which: str = "optimised") -> Table:
    table = Table(f"EQY :: {case} ({which} vs original)" if which != "optimised" else f"EQY :: {case}")
    want = expected["cases"].get(case) if which == "optimised" else None
    table.info("partitions", row["total"], f"{row['pass_count']} PASS, {len(row['fail'])} FAIL, "
               f"{len(row['unknown'])} UNKNOWN, {len(row['error'])} errored")
    if want is None:
        table.info("verdict", row["verdict"], "measured only")
        if row["fail"]:
            table.info("FAIL partitions", len(row["fail"]),
                       ", ".join(row["fail"][:8]) + (" ..." if len(row["fail"]) > 8 else ""))
        if row["unknown"]:
            table.info("UNKNOWN partitions", len(row["unknown"]),
                       ", ".join(row["unknown"][:8]) + (" ..." if len(row["unknown"]) > 8 else ""))
        return table
    table.add("verdict", want["verdict"], row["verdict"], row["verdict"] == want["verdict"])
    table.add(
        "FAIL partitions", len(want["fail"]), len(row["fail"]),
        sorted(row["fail"]) == sorted(want["fail"]),
        ", ".join(row["fail"][:8]) + (" ..." if len(row["fail"]) > 8 else ""),
    )
    table.add(
        "UNKNOWN partitions", len(want["unknown"]), len(row["unknown"]),
        sorted(row["unknown"]) == sorted(want["unknown"]),
        ", ".join(row["unknown"][:8]) + (" ..." if len(row["unknown"]) > 8 else ""),
    )
    table.add("partitions in total", want["total"], row["total"], row["total"] == want["total"])
    return table


def _show_trace(case: str, which: str, row: Dict[str, Any], args) -> None:
    run_dir = os.path.join(case_dir(args.results, case, which), "run")
    traces = counterexample_traces(run_dir, row["fail"])
    if not traces:
        print(f"[{case}] counterexample traces found: 0 (FAIL partitions: {len(row['fail'])})")
        return
    print(f"[{case}] counterexample traces: {', '.join(sorted(traces))}")
    name = args.partition or sorted(traces)[0]
    if name not in traces:
        print(f"[{case}] partition {name} is missing from the trace list above; pick one of those")
        return
    print(f"[{case}] trace of {name}: {relative(traces[name])}")
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        print("DISPLAY is unset; open the .vcd file with GTKWave on a machine with a screen")
        return
    print(f"opening {args.viewer} (close the window to finish)")
    view_waveform(traces[name], args.viewer)


def main(argv=None) -> int:
    parser = case_parser(__doc__.split("\n\n")[0])
    add_rtl_arguments(parser, gate_only=True)
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("CHIA_LEC_JOBS", min(4, os.cpu_count() or 1))),
                        help="parallel SAT jobs (default: %(default)s)")
    parser.add_argument("--depth", type=int, default=int(os.environ.get("CHIA_LEC_DEPTH", 10)),
                        help="SAT depth bound (default: %(default)s)")
    parser.add_argument("--timeout", type=int, default=None, help="seconds per EQY invocation (default: none)")
    parser.add_argument("--retries", type=int, default=2, help="resumed runs after a killed solver (default: %(default)s)")
    parser.add_argument("--analyse-only", action="store_true", help="re-read existing run directories")
    parser.add_argument("--gui", action="store_true",
                        help="open the counterexample waveform of a FAIL partition (one case)")
    parser.add_argument("--partition", default="", metavar="NAME", help="partition for --gui (default: the first FAIL)")
    parser.add_argument("--viewer", choices=("gtkwave", "surfer"), default=os.environ.get("CHIA_WAVE_VIEWER", "gtkwave"),
                        help="waveform viewer for --gui (default: %(default)s)")
    args = parser.parse_args(argv)

    cases = pick_cases(args.cases)
    selection = select_rtl(args, gate_only=True)
    which = selection.sets[0]
    specs = load_cases()
    selection.validate(cases, specs)
    if args.gui and len(cases) != 1:
        raise SystemExit("--gui shows one design: name one case, for example `tlb --gui`")
    expected = load_expected("lec")
    print(f"eqy: {tool_bin('eqy', 'EQY')}")
    report: Dict[str, Any] = {"strategy": "sat", "depth": args.depth, "gate_rtl": which, "cases": {}}
    failures = 0
    incomplete = False
    for case in cases:
        print(f"[{case}] gold: original RTL; gate: {selection.describe(case, specs[case], which)}")
        row = run_case(case, specs[case], args, selection, which)
        report["cases"][case] = row
        if row["verdict"] == "incomplete":
            incomplete = True
        table = compare(case, row, expected, which)
        print()
        print(table.render())
        print(f"[{case}] finished in {row['elapsed_s']:.0f}s")
        failures += table.failures
        if args.gui:
            _show_trace(case, which, row, args)

    out = os.path.join(args.results, "lec.json")
    write_json(out, report)
    print(f"\nwrote {relative(out)}")
    if incomplete:
        print("LEC incomplete: EQY ended before its DONE line; rerun with a longer --timeout or fewer --jobs")
        return EXIT_INCOMPLETE
    if which != "optimised":
        print(f"LEC finished on the {which} RTL; the table lists the measured partitions")
        return 1 if failures else 0
    print("LEC reproduced" if not failures else f"{failures} check(s) differ from the shipped numbers")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
