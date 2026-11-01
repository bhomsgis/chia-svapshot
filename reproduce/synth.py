#!/usr/bin/env python3
#!/usr/bin/env python3
"""Reproduce the synthesis results: Sky130 area and generic gate counts with Yosys.

Each case is synthesised on the original and the optimised RTL, or on the RTL set
that ``--rtl`` selects, with two judges:

``sky130``   yosys-slang front end, ``synth``, ``dfflibmap`` and ``abc`` against
             sky130_fd_sc_hd (tt_025C_1v80), then ``stat -liberty``. The area
             in um^2 is the paper's area result.
``generic``  technology-independent ``synth`` (abc on for div_unit, off for
             ptw and tlb), then ``stat`` and ``ltp -noff``. The cell count and
             the longest topological path (LTP) come from this judge.

Usage::

    python3 reproduce/synth.py                      # all cases, original + optimised, both judges
    python3 reproduce/synth.py ptw --judge sky130
    python3 reproduce/synth.py tlb --rtl optimised  # one RTL set
    python3 reproduce/synth.py div_unit --rtl-dir ~/my_rtl/div_unit   # your own RTL
    python3 reproduce/synth.py ptw --rtl all --rtl-dir ~/my_rtl        # shipped sets and yours
    python3 reproduce/synth.py ptw --rtl optimised --gui               # schematic in xdot
    python3 reproduce/synth.py --rtol 0.0           # exact match with the shipped numbers

``--gui`` opens the word-level schematic (after ``proc; opt``) of one case and one
RTL set in ``xdot``; ``--gui mapped`` opens the mapped Sky130 netlist instead.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reproduce.common import (  # noqa: E402
    WHICH,
    RtlSelection,
    Table,
    add_rtl_arguments,
    case_parser,
    load_cases,
    load_expected,
    pick_cases,
    relative,
    select_rtl,
    within,
    write_json,
)
from chia_svapshot.tools.yosys import (  # noqa: E402
    measure_ppa,
    view_dot,
    write_schematic,
    yosys_bin,
)


def _run(case: str, which: str, judge: str, spec: dict, abc: bool, workdir: str, selection: RtlSelection) -> dict:
    started = time.time()
    result = measure_ppa(
        selection.sources(case, spec, which),
        spec["top"],
        workdir=os.path.join(workdir, case, judge, which),
        mode=judge,
        abc=abc if judge == "generic" else None,
    )
    result["elapsed_s"] = round(time.time() - started, 1)
    liberty = result.get("liberty")
    if liberty:
        result["liberty"] = relative(liberty)
    return result


def _show(case: str, which: str, spec: dict, stage: str, viewer: str, workdir: str, selection: RtlSelection) -> int:
    """Draw the schematic of one case and RTL set in a Graphviz viewer."""
    made = write_schematic(
        selection.sources(case, spec, which), spec["top"],
        workdir=os.path.join(workdir, case, "schematic", which), stage=stage, mode="sky130",
    )
    if not made["ok"]:
        print(f"[{case}/{which}] schematic failed:\n{made['log_tail']}")
        return 1
    print(f"[{case}/{which}] {stage} schematic: {relative(made['dot'])}")
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        print("DISPLAY is unset; open the .dot file with `xdot` on a machine with a screen")
        return 0
    print(f"opening {viewer} (close the window to finish)")
    return view_dot(made["dot"], viewer)


def main(argv=None) -> int:
    parser = case_parser(__doc__.split("\n\n")[0])
    add_rtl_arguments(parser)
    parser.add_argument("--judge", choices=("sky130", "generic", "both"), default="both")
    parser.add_argument(
        "--rtol", type=float, default=0.005,
        help="relative tolerance when comparing with the shipped numbers (default: %(default)s)",
    )
    parser.add_argument(
        "--gui", nargs="?", const="rtl", choices=("rtl", "mapped"), default=None,
        help="open the schematic of one case and one RTL set in xdot (rtl = word level, mapped = Sky130 netlist)",
    )
    parser.add_argument("--viewer", default=os.environ.get("CHIA_DOT_VIEWER", "xdot"),
                        help="Graphviz viewer for --gui (default: %(default)s)")
    args = parser.parse_args(argv)

    cases = pick_cases(args.cases)
    selection = select_rtl(args)
    sets = selection.sets
    if args.gui and (len(cases) != 1 or len(sets) != 1):
        raise SystemExit("--gui shows one design: name one case and one RTL set, for example `ptw --rtl optimised --gui`")
    specs = load_cases()
    expected = load_expected("synth")
    judges = ("sky130", "generic") if args.judge == "both" else (args.judge,)
    workdir = os.path.join(args.results, "synth")
    print(f"yosys: {yosys_bin()}")

    report: dict = {"pdk": expected["pdk"], "rtol": args.rtol, "rtl_sets": list(sets)}
    failures = 0
    for judge in judges:
        report[judge] = {}
        for case in cases:
            spec = specs[case]
            abc = expected["generic"][case]["abc"]
            for which in sets:
                if judge == judges[0]:
                    print(f"[{case}] {selection.describe(case, spec, which)}")
            runs = {which: _run(case, which, judge, spec, abc, workdir, selection) for which in sets}
            report[judge][case] = runs
            table = Table(f"{judge} :: {case}")
            fields = ("cells", "wires", "area_um2") if judge == "sky130" else ("cells", "wires", "levels")
            for which in sets:
                got = runs[which]
                if not got.get("ok"):
                    table.add(f"{which} synthesis", "ok", "failed", False, (got.get("log_tail") or "")[-160:])
                    continue
                for field in fields:
                    if which in WHICH:
                        want = expected[judge][case][which][field]
                        table.add(f"{which} {field}", want, got.get(field), within(got.get(field), want, args.rtol))
                    else:
                        table.info(f"{which} {field}", got.get(field), "measured only")
            base = runs.get("original")
            if judge == "sky130" and base and base.get("ok"):
                for which in sets:
                    if which == "original" or not runs[which].get("ok"):
                        continue
                    delta = round((runs[which]["area_um2"] - base["area_um2"]) / base["area_um2"], 4)
                    if which == "optimised":
                        want = expected["sky130"][case]["area_delta"]
                        table.add("area change", want, delta, within(delta, want, max(args.rtol, 0.01)),
                                  "negative = smaller")
                    else:
                        table.info(f"{which} area change", delta, "negative = smaller")
            print()
            print(table.render())
            failures += table.failures

    out = os.path.join(args.results, "synth.json")
    write_json(out, report)
    print(f"\nwrote {relative(out)}")
    if sets == ("custom",):
        print("synthesis finished on the custom RTL; the tables list the measured values")
    else:
        print("synthesis reproduced" if not failures else f"{failures} check(s) differ from the shipped numbers")
    if args.gui:
        _show(cases[0], sets[0], specs[cases[0]], args.gui, args.viewer, workdir, selection)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
