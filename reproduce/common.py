"""Shared helpers of the reproduction scripts (synthesis, LEC, FPV)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

CASES_DIR = os.path.join(HERE, "cases")
EXPECTED_DIR = os.path.join(HERE, "expected")
RESULTS_DIR = os.environ.get("CHIA_RESULTS") or os.path.join(REPO_ROOT, "results")

WHICH = ("original", "optimised")
RTL_SETS = ("original", "optimised", "custom")


def load_cases() -> Dict[str, Dict[str, Any]]:
    with open(os.path.join(HERE, "cases.json"), encoding="utf-8") as handle:
        return json.load(handle)["cases"]


def load_expected(name: str) -> Dict[str, Any]:
    with open(os.path.join(EXPECTED_DIR, f"{name}.json"), encoding="utf-8") as handle:
        return json.load(handle)


def rtl_dir(case: str, which: str) -> str:
    return os.path.join(CASES_DIR, case, f"{which}_rtl")


def rtl_sources(case: str, which: str, spec: Optional[Dict[str, Any]] = None) -> List[str]:
    """Source files of a case in compile order (packages first)."""
    spec = spec or load_cases()[case]
    paths = [os.path.join(rtl_dir(case, which), name) for name in spec["sources"]]
    missing = [path for path in paths if not os.path.isfile(path)]
    if missing:
        raise FileNotFoundError(f"missing RTL: {missing}")
    return paths


def case_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "cases", nargs="*", metavar="CASE",
        help="div_unit, ptw, tlb (default: all three)",
    )
    parser.add_argument(
        "--results", default=RESULTS_DIR,
        help="directory for logs and result JSON (default: %(default)s)",
    )
    return parser


def add_rtl_arguments(parser: argparse.ArgumentParser, *, gate_only: bool = False) -> None:
    """``--rtl`` and ``--rtl-dir``: which RTL set an experiment runs on.

    ``gate_only=True`` serves the equivalence check, where the original RTL is
    always the gold side and ``--rtl`` picks the side under test.
    """
    group = parser.add_argument_group("RTL selection")
    if gate_only:
        choices = ("optimised", "original", "custom")
        text = (
            "RTL under test, checked against the original RTL: optimised (shipped LLM rewrite), "
            "original (identity check), or custom (--rtl-dir). Default: optimised, or custom with --rtl-dir."
        )
    else:
        choices = ("original", "optimised", "custom", "both", "all")
        text = (
            "RTL set: original, optimised (shipped LLM rewrite), custom (--rtl-dir), both (original and "
            "optimised), or all. Default: both, or custom with --rtl-dir."
        )
    group.add_argument("--rtl", "--which", dest="rtl", choices=choices, default=None, help=text)
    group.add_argument(
        "--rtl-dir", metavar="DIR", default=None,
        help=(
            "your own RTL for the custom set. DIR/<case>/ (or DIR itself for one case) holds files named like "
            "the shipped sources; the top-level <top>.sv must be present and every other file missing from DIR "
            "comes from the original RTL."
        ),
    )


@dataclass(frozen=True)
class RtlSelection:
    """The RTL sets an experiment runs on and where the custom set lives."""

    sets: Tuple[str, ...]
    custom_dir: Optional[str] = None

    def validate(self, cases: Sequence[str], specs: Dict[str, Dict[str, Any]]) -> None:
        """Fail early when the custom folder misses the top-level file of a selected case."""
        if "custom" in self.sets:
            for case in cases:
                self.case_dir(case, specs[case])

    def case_dir(self, case: str, spec: Dict[str, Any]) -> str:
        """Folder with the custom RTL of ``case``."""
        root = os.path.abspath(os.path.expanduser(self.custom_dir or ""))
        nested = os.path.join(root, case)
        base = nested if os.path.isdir(nested) else root
        top = os.path.join(base, f"{spec['top']}.sv")
        if not os.path.isfile(top):
            raise SystemExit(
                f"--rtl-dir {root}: {top} is missing; put <top>.sv directly in that folder (one case) or in {root}/<case>/"
            )
        return base

    def sources(self, case: str, spec: Dict[str, Any], which: str) -> List[str]:
        """Source files of ``case`` for one RTL set, in compile order."""
        if which != "custom":
            return rtl_sources(case, which, spec)
        base = self.case_dir(case, spec)
        paths = []
        for name in spec["sources"]:
            mine = os.path.join(base, name)
            paths.append(mine if os.path.isfile(mine) else os.path.join(rtl_dir(case, "original"), name))
        return paths

    def describe(self, case: str, spec: Dict[str, Any], which: str) -> str:
        """One line that names the origin of each source file."""
        paths = self.sources(case, spec, which)
        if which != "custom":
            return f"{which} RTL: {', '.join(os.path.basename(p) for p in paths)}"
        base = self.case_dir(case, spec)
        mine = [os.path.basename(p) for p in paths if os.path.dirname(p) == base]
        rest = [os.path.basename(p) for p in paths if os.path.dirname(p) != base]
        text = f"custom RTL from {relative(base)}: {', '.join(mine)}"
        return text + (f"; original RTL for {', '.join(rest)}" if rest else "")


def select_rtl(args: argparse.Namespace, *, gate_only: bool = False) -> RtlSelection:
    """Turn ``--rtl`` and ``--rtl-dir`` into the list of RTL sets to run."""
    choice = args.rtl
    if choice is None:
        choice = "custom" if args.rtl_dir else ("optimised" if gate_only else "both")
    if choice == "both":
        sets: Tuple[str, ...] = ("original", "optimised")
    elif choice == "all":
        sets = RTL_SETS
    else:
        sets = (choice,)
    if "custom" in sets and not args.rtl_dir:
        raise SystemExit("--rtl custom needs --rtl-dir DIR")
    if args.rtl_dir and "custom" not in sets:
        raise SystemExit(f"--rtl-dir supplies the custom set; pair it with --rtl custom or --rtl all (received --rtl {choice})")
    return RtlSelection(sets, args.rtl_dir)


def pick_cases(requested: Sequence[str]) -> List[str]:
    known = load_cases()
    names = list(requested) or list(known)
    unknown = [name for name in names if name not in known]
    if unknown:
        raise SystemExit(f"unknown case(s) {unknown}; choose from {sorted(known)}")
    return names


def within(measured: Optional[float], expected: Optional[float], rtol: float) -> bool:
    if measured is None or expected is None:
        return measured == expected
    if expected == 0:
        return measured == 0
    return abs(measured - expected) <= rtol * abs(expected)


class Table:
    """Measured-versus-expected rows printed as an aligned table."""

    def __init__(self, title: str):
        self.title = title
        self.rows: List[List[str]] = []
        self.failures = 0

    def add(self, label: str, expected: Any, measured: Any, ok: bool, note: str = "") -> bool:
        self.rows.append([label, _fmt(expected), _fmt(measured), "match" if ok else "DIFFERS", note])
        if not ok:
            self.failures += 1
        return ok

    def info(self, label: str, measured: Any, note: str = "") -> None:
        self.rows.append([label, "-", _fmt(measured), "info", note])

    def render(self) -> str:
        header = ["check", "expected", "measured", "result", "note"]
        rows = [header] + self.rows
        widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
        lines = [f"== {self.title} =="]
        for index, row in enumerate(rows):
            lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
            if index == 0:
                lines.append("  ".join("-" * w for w in widths))
        return "\n".join(lines)


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))
    if value is None:
        return "-"
    return str(value)


def write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


def relative(path: str) -> str:
    """Repository-relative form of ``path``; the absolute path when ``path`` lies outside the repository."""
    absolute = os.path.abspath(path)
    try:
        inside = os.path.commonpath([absolute, REPO_ROOT]) == REPO_ROOT
    except ValueError:
        inside = False
    return os.path.relpath(absolute, REPO_ROOT) if inside else absolute
