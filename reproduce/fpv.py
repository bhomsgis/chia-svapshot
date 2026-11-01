#!/usr/bin/env python3
"""Reproduce the FPV results with VC Formal: proofs and coverage.

For each case the retained SVApshot properties (``cases/<case>/sva`` and
``bind``) run against the original RTL and against the optimised RTL. The
script reports

* proofs         assertions proved with a non-vacuous antecedent (VC Formal
                 ``report_fv -verbose`` and the ``PROP_I_RESULT`` log lines),
* density        registers and primary inputs inside the cone of influence of
                 at least one assertion (``report_assertion_density``),
* formal core    registers and inputs the proofs used (``report_formal_core``),
* report_cov     line, condition, FSM, and toggle goals that the structural
                 formal-core coverage marks as covered (``report_cov``),
* structural COI the share of design signals inside an assertion cone, computed
                 by static analysis of the RTL text (``chia_svapshot.analysis.coi``).

Where ``vcf`` runs
------------------

The script picks the place in this order: ``--docker NAME`` (or ``CHIA_VCF_DOCKER``), the
inside of the container when this shell already runs in one, the running ``vcformal*``
container when this shell is on the host, and finally ``VC_FORMAL_HOME/bin/vcf`` or ``PATH``
(default install folder ``~/synopsys/ins/vc_static/<version>``). ``--no-docker`` forces the
host. The license comes from ``SNPSLMD_LICENSE_FILE`` (the container keeps its own value).

Usage::

    python3 reproduce/fpv.py                            # three cases, original + optimised
    python3 reproduce/fpv.py ptw --rtl optimised
    python3 reproduce/fpv.py tlb --rtl-dir ~/my_rtl/tlb # your own RTL against the shipped properties
    python3 reproduce/fpv.py --no-coverage              # proofs only
    python3 reproduce/fpv.py --signoff                  # add the OC/FC signoff step
    python3 reproduce/fpv.py ptw --rtl optimised --gui  # VC Formal GUI on one design
    python3 reproduce/fpv.py --analyse-only             # re-read earlier results/fpv runs
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reproduce.common import (  # noqa: E402
    CASES_DIR,
    WHICH,
    RtlSelection,
    Table,
    add_rtl_arguments,
    case_parser,
    load_cases,
    load_expected,
    pick_cases,
    relative,
    rtl_dir,
    select_rtl,
    write_json,
)
from chia_svapshot.analysis.coi import analyse_snapshot_cones, assertion_name  # noqa: E402
from chia_svapshot.analysis.proof_status import (  # noqa: E402
    infrastructure_failure_in_log,
    license_failure_in_log,
    load_vcformal_reports,
    merge_reports_into_result,
    parse_vcformal_log,
)
from chia_svapshot.ppa_rtl import extract_assertions  # noqa: E402
from chia_svapshot.tools.vcf import (  # noqa: E402
    VcfTarget,
    ensure_visible,
    file_list,
    fpv_script,
    resolve_target,
    run_batch,
    run_gui,
)

EXIT_LICENSE = 3
EXIT_NO_TOOL = 2

_COV_LABELS = {
    "Line Coverage": "line",
    "Condition Coverage": "condition",
    "FSM State Coverage": "fsm_state",
    "FSM Transition Coverage": "fsm_transition",
    "Toggle Coverage": "toggle",
}
_COV_RE = re.compile(
    r"> (" + "|".join(_COV_LABELS) + r")\s+- # found\s*:\s*(\d+)\s+- # covered\s*:\s*(\d+)"
)


def parse_report_cov(text: str) -> Optional[Dict[str, List[int]]]:
    """``[covered, total]`` per goal class from a ``report_cov`` summary, or ``None``."""
    section = text.split("Coverage Summary:", 1)
    if len(section) < 2:
        return None
    found = {
        _COV_LABELS[m.group(1)]: [int(m.group(3)), int(m.group(2))]
        for m in _COV_RE.finditer(section[1])
    }
    return found or None


def structural_coi(case: str, spec: Dict[str, Any], top_file: Optional[str] = None) -> Dict[str, Any]:
    """Tool-independent cone coverage of the retained assertions on one RTL file (default: the original)."""
    top = spec["top"]
    with open(os.path.join(CASES_DIR, case, "sva", f"{top}_prop.sv"), encoding="utf-8") as handle:
        assertions = [row for row in extract_assertions(handle.read()) if assertion_name(row)]
    top_file = top_file or os.path.join(rtl_dir(case, "original"), f"{top}.sv")
    with open(top_file, encoding="utf-8") as handle:
        rtl = handle.read()
    metrics = analyse_snapshot_cones(assertions, rtl, top)["metrics"]
    return {
        "coverage": round(metrics["coi_coverage"], 4),
        "signals": metrics["design_signal_count"],
        "union_cone": metrics["union_cone_size"],
        "assertions": metrics["property_count"],
    }


def stage(
    case: str,
    which: str,
    spec: Dict[str, Any],
    workdir: str,
    *,
    coverage: bool,
    signoff: bool,
    selection: Optional[RtlSelection] = None,
    gui: bool = False,
) -> None:
    """Lay out sources, properties, file list, and Tcl for one run."""
    selection = selection or RtlSelection(WHICH)
    shutil.rmtree(workdir, ignore_errors=True)
    rtl_copy = os.path.join(workdir, "rtl")
    os.makedirs(rtl_copy)
    sources = []
    for path in selection.sources(case, spec, which):
        dest = os.path.join(rtl_copy, os.path.basename(path))
        shutil.copy2(path, dest)
        sources.append(dest)
    sva = os.path.join(workdir, "sva")
    os.makedirs(sva)
    prop = os.path.join(sva, f"{spec['top']}_prop.sv")
    bind = os.path.join(sva, f"{spec['top']}_bind.svh")
    shutil.copy2(os.path.join(CASES_DIR, case, "sva", f"{spec['top']}_prop.sv"), prop)
    shutil.copy2(os.path.join(CASES_DIR, case, "bind", f"{spec['top']}_bind.svh"), bind)
    with open(os.path.join(workdir, "files_vcf.vc"), "w", encoding="utf-8") as handle:
        handle.write(file_list(sources, prop, bind, rtl_copy))
    with open(os.path.join(workdir, "FPV_vcf.tcl"), "w", encoding="utf-8") as handle:
        handle.write(
            fpv_script(
                spec["top"], spec["clock"], spec["reset"], spec["reset_sense"],
                coverage=coverage, signoff=signoff, gui=gui,
            )
        )


def analyse(workdir: str, log_text: str) -> Dict[str, Any]:
    """Proof counts, density, formal core, and ``report_cov`` of one finished run."""
    report_dir = os.path.join(workdir, "reports")
    reports = load_vcformal_reports(report_dir)
    result = parse_vcformal_log(log_text)
    result = merge_reports_into_result(result, reports)
    counts = result.counts_by_qualification()
    coverage = result.tool_checker_coverage()

    def pair(covered_key: str, total_key: str) -> Optional[List[int]]:
        if total_key not in coverage:
            return None
        return [int(coverage.get(covered_key, 0)), int(coverage[total_key])]

    cov_path = os.path.join(report_dir, "cov.rpt")
    cov_text = ""
    if os.path.isfile(cov_path):
        with open(cov_path, encoding="utf-8", errors="replace") as handle:
            cov_text = handle.read()
    return {
        "assertions": len(result.records),
        "proved_non_vacuous": counts.get("proved_non_vacuous", 0),
        "proved_vacuous": counts.get("proved_vacuous", 0),
        "failing": counts.get("failing_property_mismatch", 0) + counts.get("failing_missing_assumption", 0),
        "inconclusive": counts.get("inconclusive", 0),
        "property_density": {
            "registers": pair("property_density_covered", "property_density_total"),
            "inputs": pair("property_density_pi_covered", "property_density_pi_total"),
        },
        "formal_core": {
            "registers": (
                [int(coverage.get("formal_core_coverage_count", 0)), int(coverage["property_density_total"])]
                if "property_density_total" in coverage else None
            ),
            "inputs": (
                [int(coverage.get("formal_core_coverage_pi_count", 0)), int(coverage["property_density_pi_total"])]
                if "property_density_pi_total" in coverage else None
            ),
        },
        "report_cov": parse_report_cov(cov_text),
        "license_failure": license_failure_in_log(log_text),
        "infrastructure_failure": infrastructure_failure_in_log(log_text),
        "compile_failed": bool(result.summary.compile_failed),
        "tool_version": result.summary.tool_version,
    }


def _pair_ok(measured: Optional[List[int]], expected: Optional[List[int]]) -> bool:
    return measured is not None and expected is not None and list(measured) == list(expected)


def compare(
    case: str,
    measured: Dict[str, Dict[str, Any]],
    coi: Dict[str, Any],
    expected: Dict[str, Any],
    coi_custom: Optional[Dict[str, Any]] = None,
) -> Table:
    table = Table(f"FPV :: {case}")
    want = expected["cases"][case]
    for which, got in measured.items():
        note = f"{got['assertions']} assertions, {got['failing']} failing, {got['inconclusive']} inconclusive"
        if which in WHICH:
            table.add(
                f"{which}: assertions proved non-vacuous",
                want["proved_non_vacuous"][which], got["proved_non_vacuous"],
                got["proved_non_vacuous"] == want["proved_non_vacuous"][which], note,
            )
        else:
            table.info(f"{which}: assertions proved non-vacuous", got["proved_non_vacuous"], note)
    table.add(
        "structural COI (original RTL)",
        want["structural_coi_gold"]["coverage"], coi["coverage"],
        coi["coverage"] == want["structural_coi_gold"]["coverage"],
        f"{coi['union_cone']}/{coi['signals']} signals",
    )
    if coi_custom is not None:
        table.info("structural COI (custom RTL)", coi_custom["coverage"],
                   f"{coi_custom['union_cone']}/{coi_custom['signals']} signals")
    for which, got in measured.items():
        for label, key, sub in (
            ("density: registers", "property_density", "registers"),
            ("density: inputs", "property_density", "inputs"),
            ("formal core: registers", "formal_core", "registers"),
            ("formal core: inputs", "formal_core", "inputs"),
        ):
            name = f"{which}: {label} [covered, total]"
            if which in WHICH:
                table.add(name, want[key][sub], got[key][sub], _pair_ok(got[key][sub], want[key][sub]))
            else:
                table.info(name, got[key][sub])
        want_cov = (want.get("report_cov") or {}).get(which)
        if which not in WHICH or want_cov is None:
            table.info(f"{which}: report_cov [covered, total]", got["report_cov"], "measured only")
            continue
        for goal, values in want_cov.items():
            got_cov = (got["report_cov"] or {}).get(goal)
            table.add(f"{which}: report_cov {goal}", values, got_cov, _pair_ok(got_cov, values))
    return table


def _choose_target(args) -> VcfTarget:
    choice = "off" if args.no_docker else args.docker
    try:
        return resolve_target(choice, auto=True)
    except RuntimeError as exc:
        raise SystemExit(f"VC Formal location: {exc}")


def main(argv=None) -> int:
    parser = case_parser(__doc__.split("\n\n")[0])
    add_rtl_arguments(parser)
    parser.add_argument("--no-coverage", action="store_true", help="proofs only")
    parser.add_argument("--signoff", action="store_true", help="add the OC/FC signoff step")
    parser.add_argument("--timeout", type=int, default=None, help="seconds per VC Formal run")
    where = parser.add_argument_group("where vcf runs")
    where.add_argument("--docker", default=None, metavar="NAME",
                       help="run vcf in this container with `docker exec` (default: the running vcformal* "
                            "container when this shell is on the host; also CHIA_VCF_DOCKER)")
    where.add_argument("--no-docker", action="store_true",
                       help="run vcf on this machine even when a vcformal container is running")
    parser.add_argument("--gui", action="store_true",
                        help="open the VC Formal GUI (vcf -gui) on one case and one RTL set instead of a batch run")
    parser.add_argument("--analyse-only", action="store_true", help="re-read existing run directories")
    args = parser.parse_args(argv)

    cases = pick_cases(args.cases)
    selection = select_rtl(args)
    sets = selection.sets
    specs = load_cases()
    selection.validate(cases, specs)
    if args.gui and (len(cases) != 1 or len(sets) != 1):
        raise SystemExit("--gui shows one design: name one case and one RTL set, for example `ptw --rtl optimised --gui`")
    if args.gui and args.analyse_only:
        raise SystemExit("--gui starts a run; drop --analyse-only")
    expected = load_expected("fpv")
    coverage = not args.no_coverage
    report: Dict[str, Any] = {"vc_formal_reference": expected["vc_formal"], "rtl_sets": list(sets), "cases": {}}
    failures = 0
    license_problem = False

    target = None
    if not args.analyse_only:
        target = _choose_target(args)
        print(f"vcf: {target.describe()}")
        if not target.container and not (os.path.isfile(target.binary) or shutil.which(target.binary)):
            print("vcf is missing: set VC_FORMAL_HOME, start the vcformal container, or pass --docker NAME")
            return EXIT_NO_TOOL
        try:
            ensure_visible(target, args.results)
        except RuntimeError as exc:
            print(exc)
            return EXIT_NO_TOOL

    for case in cases:
        spec = specs[case]
        measured: Dict[str, Dict[str, Any]] = {}
        for which in sets:
            workdir = os.path.join(args.results, "fpv", f"{case}_{which}")
            log_path = os.path.join(workdir, "vcf.log")
            if not args.analyse_only:
                print(f"[{case}/{which}] {selection.describe(case, spec, which)}")
                stage(case, which, spec, workdir, coverage=coverage, signoff=args.signoff,
                      selection=selection, gui=args.gui)
                if args.gui:
                    print(f"[{case}/{which}] opening the VC Formal GUI in {relative(workdir)} (close it to finish)", flush=True)
                    code = run_gui(workdir, "FPV_vcf.tcl", "vcst_rtdb", target=target)
                    print(f"[{case}/{which}] GUI closed (exit {code})")
                    if not os.path.isfile(os.path.join(workdir, "reports", "properties.rpt")):
                        print(f"[{case}/{which}] the GUI session wrote 0 reports; let check_fv finish before closing")
                        return 0
                    log_text = ""
                else:
                    print(f"[{case}/{which}] VC Formal ...", flush=True)
                    started = time.time()
                    code = run_batch(
                        workdir, "FPV_vcf.tcl", "vcst_rtdb", log_path,
                        timeout_s=args.timeout, target=target,
                    )
                    print(f"[{case}/{which}] finished in {time.time() - started:.0f}s (exit {code}); log {relative(log_path)}")
            if not args.gui:
                if not os.path.isfile(log_path):
                    print(f"[{case}/{which}] log missing at {relative(log_path)}")
                    failures += 1
                    continue
                with open(log_path, encoding="utf-8", errors="replace") as handle:
                    log_text = handle.read()
            row = analyse(workdir, log_text)
            measured[which] = row
            if row["license_failure"] or row["infrastructure_failure"]:
                license_problem = True
                print(
                    f"[{case}/{which}] VC Formal stopped before proving: "
                    + ("license checkout failed" if row["license_failure"] else "session database unavailable")
                    + ". Connect to the license server and rerun."
                )
        coi = structural_coi(case, spec)
        coi_custom = None
        if "custom" in sets:
            top_file = next(p for p in selection.sources(case, spec, "custom") if os.path.basename(p) == f"{spec['top']}.sv")
            coi_custom = structural_coi(case, spec, top_file)
        report["cases"][case] = {"structural_coi": coi, "runs": measured}
        if license_problem:
            continue
        table = compare(case, measured, coi, expected, coi_custom)
        print()
        print(table.render())
        failures += table.failures

    out = os.path.join(args.results, "fpv.json")
    write_json(out, report)
    print(f"\nwrote {relative(out)}")
    if license_problem:
        print("FPV stopped: VC Formal reported a license failure")
        return EXIT_LICENSE
    if sets == ("custom",):
        print("FPV finished on the custom RTL; the table lists the measured values")
        return 1 if failures else 0
    print("FPV reproduced" if not failures else f"{failures} check(s) differ from the shipped numbers")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
