#!/usr/bin/env python3
"""The reproduction scripts: data layout, report parsing, RTL selection, and tool plumbing."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reproduce import common, fpv, lec  # noqa: E402
from chia_svapshot.tools import tool_bin  # noqa: E402
from chia_svapshot.tools import vcf  # noqa: E402
from chia_svapshot.tools.eqy import classify_partitions, counterexample_traces, parse_eqy_log  # noqa: E402
from chia_svapshot.tools.vcf import file_list, fpv_script  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "vcf"
CASES = ("div_unit", "ptw", "tlb")


def _args(*argv, gate_only=False):
    parser = argparse.ArgumentParser()
    common.add_rtl_arguments(parser, gate_only=gate_only)
    return parser.parse_args(list(argv))


class LayoutTest(unittest.TestCase):
    def test_every_case_ships_both_rtl_sets_and_the_properties(self):
        for case, spec in common.load_cases().items():
            for which in common.WHICH:
                for path in common.rtl_sources(case, which, spec):
                    self.assertTrue(os.path.isfile(path), path)
            self.assertTrue((ROOT / "reproduce" / "cases" / case / "sva" / f"{spec['top']}_prop.sv").is_file())
            self.assertTrue((ROOT / "reproduce" / "cases" / case / "bind" / f"{spec['top']}_bind.svh").is_file())

    def test_expected_files_cover_the_cases(self):
        for name in ("synth", "fpv"):
            data = common.load_expected(name)
            for case in CASES:
                self.assertIn(case, json.dumps(data))
        synth = common.load_expected("synth")
        for case in CASES:
            self.assertLess(synth["sky130"][case]["area_delta"], 0)
            self.assertLess(
                synth["sky130"][case]["optimised"]["area_um2"],
                synth["sky130"][case]["original"]["area_um2"],
            )
        fpv_expected = common.load_expected("fpv")
        for case in CASES:
            for which in common.WHICH:
                self.assertIn("line", fpv_expected["cases"][case]["report_cov"][which])

    def test_liberty_ships_with_the_repository(self):
        from chia_svapshot.tools import DEFAULT_LIBERTY

        self.assertTrue(os.path.isfile(DEFAULT_LIBERTY))

    def test_relative_keeps_outside_paths_absolute(self):
        self.assertEqual(common.relative("/tmp/somewhere/x"), "/tmp/somewhere/x")
        self.assertEqual(common.relative(str(ROOT / "results" / "a")), os.path.join("results", "a"))


class RtlSelectionTest(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(common.select_rtl(_args()).sets, ("original", "optimised"))
        self.assertEqual(common.select_rtl(_args(gate_only=True), gate_only=True).sets, ("optimised",))

    def test_named_sets(self):
        self.assertEqual(common.select_rtl(_args("--rtl", "original")).sets, ("original",))
        self.assertEqual(common.select_rtl(_args("--which", "optimised")).sets, ("optimised",))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(common.select_rtl(_args("--rtl", "all", "--rtl-dir", tmp)).sets, common.RTL_SETS)

    def test_rtl_dir_alone_selects_the_custom_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(common.select_rtl(_args("--rtl-dir", tmp)).sets, ("custom",))
            gate = _args("--rtl-dir", tmp, gate_only=True)
            self.assertEqual(common.select_rtl(gate, gate_only=True).sets, ("custom",))

    def test_inconsistent_flags_are_reported(self):
        with self.assertRaises(SystemExit):
            common.select_rtl(_args("--rtl", "custom"))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                common.select_rtl(_args("--rtl", "original", "--rtl-dir", tmp))

    def test_custom_files_replace_the_original_and_the_rest_falls_back(self):
        specs = common.load_cases()
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "tlb"))
            shutil.copy2(ROOT / "reproduce/cases/tlb/optimised_rtl/tlb.sv", os.path.join(tmp, "tlb", "tlb.sv"))
            selection = common.select_rtl(_args("--rtl-dir", tmp))
            selection.validate(["tlb"], specs)
            paths = selection.sources("tlb", specs["tlb"], "custom")
            self.assertEqual([os.path.basename(p) for p in paths], specs["tlb"]["sources"])
            self.assertTrue(paths[-1].startswith(tmp))
            self.assertIn("original_rtl", paths[0])
            self.assertIn("tlb.sv", selection.describe("tlb", specs["tlb"], "custom"))

    def test_a_single_case_folder_without_the_top_file_is_rejected(self):
        specs = common.load_cases()
        with tempfile.TemporaryDirectory() as tmp:
            selection = common.select_rtl(_args("--rtl-dir", tmp))
            with self.assertRaises(SystemExit):
                selection.validate(["ptw"], specs)

    def test_a_flat_folder_serves_one_case(self):
        specs = common.load_cases()
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy2(ROOT / "reproduce/cases/ptw/optimised_rtl/ptw.sv", tmp)
            selection = common.select_rtl(_args("--rtl-dir", tmp))
            selection.validate(["ptw"], specs)
            with self.assertRaises(SystemExit):
                selection.validate(["ptw", "tlb"], specs)


class VcfScriptTest(unittest.TestCase):
    def test_script_carries_the_coverage_flow(self):
        text = fpv_script("ptw", "clk_i", "rstn_i", "low")
        for needle in (
            "elaborate -cov all -sva $top",
            "set_fml_var fml_vacuity_on true",
            "create_clock clk_i -period 100",
            "create_reset rstn_i -sense low",
            "check_fv -block",
            "compute_formal_core -block",
            "report_assertion_density -list -status all -type reg",
            "compute_formal_core_coverage -par_task FPV -structural -block",
            "-mark fcore_plus_coi",
            "report_cov",
        ):
            self.assertIn(needle, text)
        self.assertNotIn("signoff_config", text)
        self.assertIn("signoff_config", fpv_script("ptw", "clk_i", "rstn_i", "low", signoff=True))

    def test_proof_only_script_skips_coverage_commands(self):
        text = fpv_script("tlb", "clk_i", "rstn_i", "low", coverage=False)
        self.assertIn("set collect_coverage 0", text)
        self.assertIn("if {$collect_coverage} {", text)

    def test_gui_script_keeps_the_session_open(self):
        batch = fpv_script("tlb", "clk_i", "rstn_i", "low")
        gui = fpv_script("tlb", "clk_i", "rstn_i", "low", gui=True)
        self.assertTrue(batch.rstrip().endswith("exit"))
        self.assertNotIn("\nexit\n", gui)
        self.assertIn("the GUI stays open", gui)

    def test_high_reset_sense(self):
        self.assertIn("-sense high", fpv_script("m", "clk", "rst", "high"))

    def test_file_list_orders_sources_then_checker_then_bind(self):
        text = file_list(["/w/a_pkg.sv", "/w/m.sv"], "/w/sva/m_prop.sv", "/w/sva/m_bind.svh", "/w")
        lines = text.splitlines()
        self.assertLess(lines.index("/w/a_pkg.sv"), lines.index("/w/m.sv"))
        self.assertLess(lines.index("/w/m.sv"), lines.index("/w/sva/m_prop.sv"))
        self.assertLess(lines.index("/w/sva/m_prop.sv"), lines.index("/w/sva/m_bind.svh"))
        self.assertIn("+incdir+/w", lines)


class VcfTargetTest(unittest.TestCase):
    def test_off_runs_on_this_machine_even_with_a_container_running(self):
        with mock.patch.object(vcf, "running_containers", return_value=["vcformal-dev"]):
            self.assertEqual(vcf.resolve_target("off", auto=True).container, "")

    def test_auto_takes_the_running_container_from_the_host(self):
        with mock.patch.object(vcf, "inside_container", return_value=False), \
                mock.patch.object(vcf, "running_containers", return_value=["vcformal-dev", "vcformal-dev-2"]), \
                mock.patch.object(vcf, "_container_home", return_value="/opt/vcf"):
            target = vcf.resolve_target(None, auto=True)
        self.assertEqual((target.container, target.home), ("vcformal-dev", "/opt/vcf"))
        self.assertIn("docker exec vcformal-dev", target.describe())

    def test_inside_a_container_vcf_runs_there(self):
        with mock.patch.object(vcf, "inside_container", return_value=True), \
                mock.patch.object(vcf, "running_containers", return_value=["vcformal-dev"]):
            self.assertEqual(vcf.resolve_target(None, auto=True).container, "")

    def test_environment_choice_applies_without_the_auto_flag(self):
        with mock.patch.dict(os.environ, {"CHIA_VCF_DOCKER": "mybox"}), \
                mock.patch.object(vcf, "_container_home", return_value="/opt/vcf"):
            self.assertEqual(vcf.resolve_target().container, "mybox")
        with mock.patch.dict(os.environ, {"CHIA_VCF_DOCKER": ""}), \
                mock.patch.object(vcf, "running_containers", return_value=["vcformal-dev"]):
            self.assertEqual(vcf.resolve_target().container, "")

    def test_docker_command_forwards_set_variables_only(self):
        target = vcf.VcfTarget("vcformal-dev", "/opt/vcf")
        env = {"PATH": os.environ["PATH"], "SNPSLMD_LICENSE_FILE": "27000@lic"}
        with mock.patch.dict(os.environ, env, clear=True):
            cmd, cwd, _ = vcf.command(target, ["-f", "a.tcl", "-batch"], "/work/run")
        self.assertIsNone(cwd)
        self.assertEqual(cmd[:2], ["docker", "exec"])
        self.assertIn("SNPSLMD_LICENSE_FILE=27000@lic", cmd)
        self.assertFalse([part for part in cmd if part.startswith("LM_LICENSE_FILE=")])
        self.assertEqual(cmd[cmd.index("-w") + 1], "/work/run")
        self.assertEqual(cmd[-4:], ["/opt/vcf/bin/vcf", "-f", "a.tcl", "-batch"][-4:])
        self.assertNotIn("DISPLAY=:0", cmd)

    def test_an_empty_license_variable_stays_with_the_container(self):
        target = vcf.VcfTarget("vcformal-dev", "/opt/vcf")
        with mock.patch.dict(os.environ, {"PATH": os.environ["PATH"]}, clear=True):
            cmd, _, _ = vcf.command(target, ["-batch"], "/work")
        self.assertFalse([part for part in cmd if part.startswith("SNPSLMD_LICENSE_FILE")])

    def test_gui_command_carries_the_display(self):
        target = vcf.VcfTarget("vcformal-dev", "/opt/vcf")
        with mock.patch.dict(os.environ, {"PATH": os.environ["PATH"], "DISPLAY": ":7"}, clear=True), \
                mock.patch.object(sys.stdin, "isatty", return_value=True, create=True):
            cmd, _, _ = vcf.command(target, ["-gui"], "/work", gui=True)
        self.assertIn("-it", cmd)
        self.assertIn("DISPLAY=:7", cmd)
        self.assertFalse([part for part in cmd if part.startswith("TERMINFO")])

    def test_local_command_runs_in_the_work_folder(self):
        target = vcf.VcfTarget("", "/opt/vcf")
        cmd, cwd, env = vcf.command(target, ["-batch"], "/work/run")
        self.assertEqual((cmd, cwd), (["/opt/vcf/bin/vcf", "-batch"], "/work/run"))
        self.assertTrue(env.get("TERM"))

    def test_default_install_folder_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "synopsys", "ins", "vc_static", "X-9999.01")
            os.makedirs(os.path.join(home, "bin"))
            Path(home, "bin", "vcf").write_text("#!/bin/sh\n")
            with mock.patch.dict(os.environ, {"HOME": tmp, "PATH": "/nonexistent"}, clear=True):
                self.assertEqual(vcf.install_home(), home)
                self.assertEqual(vcf.vcf_binary(), os.path.join(home, "bin", "vcf"))


class FpvAnalysisTest(unittest.TestCase):
    def _analyse(self, case, which):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(FIXTURES / case / which, os.path.join(tmp, "reports"))
            return fpv.analyse(tmp, "")

    def test_recorded_reports_reproduce_the_shipped_numbers(self):
        expected = common.load_expected("fpv")
        specs = common.load_cases()
        for case in CASES:
            measured = {which: self._analyse(case, which) for which in common.WHICH}
            table = fpv.compare(case, measured, fpv.structural_coi(case, specs[case]), expected)
            self.assertEqual(table.failures, 0, table.render())

    def test_report_cov_parser(self):
        text = (FIXTURES / "ptw" / "optimised" / "cov.rpt").read_text()
        self.assertEqual(
            fpv.parse_report_cov(text),
            {
                "line": [69, 69], "condition": [133, 133], "fsm_state": [10, 10],
                "fsm_transition": [21, 21], "toggle": [1506, 1506],
            },
        )
        self.assertIsNone(fpv.parse_report_cov("no coverage here"))

    def test_the_rtl_sets_differ_in_goal_totals(self):
        original = self._analyse("ptw", "original")["report_cov"]
        optimised = self._analyse("ptw", "optimised")["report_cov"]
        self.assertNotEqual(original["line"], optimised["line"])

    def test_a_missing_proof_is_reported(self):
        expected = common.load_expected("fpv")
        specs = common.load_cases()
        row = dict(self._analyse("tlb", "optimised"), proved_non_vacuous=13)
        table = fpv.compare("tlb", {"optimised": row}, fpv.structural_coi("tlb", specs["tlb"]), expected)
        self.assertGreater(table.failures, 0)

    def test_custom_runs_are_informational(self):
        expected = common.load_expected("fpv")
        specs = common.load_cases()
        row = self._analyse("tlb", "optimised")
        table = fpv.compare(
            "tlb", {"custom": row}, fpv.structural_coi("tlb", specs["tlb"]), expected,
            coi_custom=fpv.structural_coi("tlb", specs["tlb"]),
        )
        self.assertEqual(table.failures, 0)
        self.assertIn("custom: assertions proved non-vacuous", table.render())

    def test_stage_writes_inputs_for_one_run(self):
        spec = common.load_cases()["tlb"]
        with tempfile.TemporaryDirectory() as tmp:
            fpv.stage("tlb", "optimised", spec, tmp, coverage=True, signoff=False)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "FPV_vcf.tcl")))
            files = Path(tmp, "files_vcf.vc").read_text().splitlines()
            rtl = [line for line in files if line.startswith("/") and line.endswith(".sv")]
            self.assertEqual(len(rtl), 4)  # three RTL files and the checker
            self.assertEqual([os.path.basename(p) for p in rtl][:3], ["mmu_pkg.sv", "pseudoLRU.sv", "tlb.sv"])
            self.assertEqual(os.path.basename(rtl[3]), "tlb_prop.sv")
            self.assertTrue(files[-1].endswith("tlb_bind.svh"))

    def test_stage_uses_the_custom_files(self):
        spec = common.load_cases()["tlb"]
        with tempfile.TemporaryDirectory() as tmp:
            mine = os.path.join(tmp, "mine")
            os.makedirs(mine)
            marker = "// custom marker\n" + (ROOT / "reproduce/cases/tlb/optimised_rtl/tlb.sv").read_text()
            Path(mine, "tlb.sv").write_text(marker)
            selection = common.select_rtl(_args("--rtl-dir", mine))
            work = os.path.join(tmp, "work")
            fpv.stage("tlb", "custom", spec, work, coverage=False, signoff=False, selection=selection)
            self.assertTrue(Path(work, "rtl", "tlb.sv").read_text().startswith("// custom marker"))
            self.assertTrue(os.path.isfile(os.path.join(work, "rtl", "mmu_pkg.sv")))


class FpvEndToEndTest(unittest.TestCase):
    """Runs fpv.py against a stand-in ``vcf`` that writes the recorded reports."""

    def _fake_vcf_home(self, tmp, case, which="optimised", license_ok=True):
        home = os.path.join(tmp, "vcf_home")
        os.makedirs(os.path.join(home, "bin"))
        script = os.path.join(home, "bin", "vcf")
        record = os.path.join(tmp, "vcf_argv.txt")
        if license_ok:
            body = (
                "#!/bin/sh\n"
                f"echo \"$@\" >> {record}\n"
                "mkdir -p reports\n"
                f"cp {FIXTURES / case / which}/* reports/\n"
                "echo 'Version X-2025.06-SP2 for linux64 - test'\n"
            )
        else:
            body = (
                "#!/bin/sh\n"
                "echo '*** Error: License Server, unable to checkout required license(s)'\n"
                "exit 1\n"
            )
        with open(script, "w") as handle:
            handle.write(body)
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR)
        return home, record

    def _run(self, tmp, home, *argv):
        env = {"VC_FORMAL_HOME": home, "PATH": os.environ["PATH"], "HOME": tmp}
        with mock.patch.dict(os.environ, env, clear=True):
            return fpv.main(list(argv) + ["--no-docker", "--results", os.path.join(tmp, "results")])

    def test_fpv_script_reproduces_with_recorded_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, record = self._fake_vcf_home(tmp, "tlb")
            code = self._run(tmp, home, "tlb", "--rtl", "optimised")
            self.assertEqual(code, 0)
            report = json.loads(Path(tmp, "results", "fpv.json").read_text())
            self.assertEqual(report["cases"]["tlb"]["runs"]["optimised"]["proved_non_vacuous"], 14)
            self.assertIn("-batch", Path(record).read_text())

    def test_gui_flag_opens_vcf_with_the_gui_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, record = self._fake_vcf_home(tmp, "tlb")
            code = self._run(tmp, home, "tlb", "--rtl", "optimised", "--gui")
            self.assertEqual(code, 0)
            argv = Path(record).read_text()
            self.assertIn("-gui", argv)
            self.assertNotIn("-batch", argv)
            tcl = Path(tmp, "results", "fpv", "tlb_optimised", "FPV_vcf.tcl").read_text()
            self.assertNotIn("\nexit\n", tcl)

    def test_gui_needs_one_design(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, _ = self._fake_vcf_home(tmp, "tlb")
            with self.assertRaises(SystemExit):
                self._run(tmp, home, "tlb", "--gui")
            with self.assertRaises(SystemExit):
                self._run(tmp, home, "--rtl", "optimised", "--gui")

    def test_custom_rtl_runs_against_the_shipped_properties(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, _ = self._fake_vcf_home(tmp, "tlb")
            mine = os.path.join(tmp, "mine")
            os.makedirs(mine)
            shutil.copy2(ROOT / "reproduce/cases/tlb/optimised_rtl/tlb.sv", mine)
            code = self._run(tmp, home, "tlb", "--rtl-dir", mine)
            self.assertEqual(code, 0)
            self.assertTrue(os.path.isdir(os.path.join(tmp, "results", "fpv", "tlb_custom")))

    def test_missing_license_gets_its_own_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, _ = self._fake_vcf_home(tmp, "tlb", license_ok=False)
            code = self._run(tmp, home, "tlb", "--rtl", "optimised")
            self.assertEqual(code, fpv.EXIT_LICENSE)

    def test_missing_tool_gets_its_own_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"PATH": "/nonexistent", "HOME": tmp}
            with mock.patch.dict(os.environ, env, clear=True):
                code = fpv.main(["tlb", "--rtl", "optimised", "--no-docker", "--results", os.path.join(tmp, "r")])
            self.assertEqual(code, fpv.EXIT_NO_TOOL)


class EqyParsingTest(unittest.TestCase):
    SUMMARY = (
        "EQY 00:00:01 [run] run: Proved equivalence of partition 'top.a' using strategy 'simple'\n"
        "EQY 00:00:01 [run] run: Could not prove equivalence of partition 'top.b' using strategy 'simple': "
        "partitions not equivalent\n"
        "EQY 00:00:01 [run] run: Could not prove equivalence of partition 'top.c' using strategy 'simple': "
        "unknown result\n"
        "EQY 00:00:02 [run] Warning: Failed to prove equivalence for 1/3 partitions:\n"
        "EQY 00:00:02 [run] Failed to prove equivalence of partition top.b\n"
        "EQY 00:00:02 [run] DONE (FAIL, rc=2)\n"
    )

    def test_partition_classes(self):
        row = classify_partitions(self.SUMMARY)
        self.assertEqual(row["pass"], ["top.a"])
        self.assertEqual(row["fail"], ["top.b"])
        self.assertEqual(row["unknown"], ["top.c"])
        self.assertTrue(row["done"])
        self.assertEqual(lec.verdict(dict(row)), "inequivalent")

    def test_killed_solver_is_an_error_and_the_run_stays_open(self):
        text = (
            "EQY [run] run: Execution of strategy 'simple' on partition 'top.m.1' encountered an error.\n"
            "EQY [run] run: finished (returncode=2)\n"
        )
        row = classify_partitions(text)
        self.assertEqual(row["error"], ["top.m.1"])
        self.assertFalse(row["done"])
        self.assertEqual(lec.verdict(dict(row)), "incomplete")

    def test_log_without_summary_stays_inconclusive(self):
        self.assertTrue(parse_eqy_log(self.SUMMARY.split("EQY 00:00:02")[0])["inconclusive"])


class LecSelectionTest(unittest.TestCase):
    def test_working_folders_keep_the_shipped_name_for_the_optimised_check(self):
        self.assertTrue(lec.case_dir("/r", "tlb", "optimised").endswith(os.path.join("lec", "tlb")))
        self.assertTrue(lec.case_dir("/r", "tlb", "custom").endswith(os.path.join("lec", "tlb_custom")))

    def test_other_gate_sets_are_measured_only(self):
        row = {
            "total": 3, "pass_count": 2, "fail": ["top.b"], "unknown": [], "error": [], "verdict": "inequivalent",
        }
        table = lec.compare("tlb", row, common.load_expected("lec"), "custom")
        self.assertEqual(table.failures, 0)
        self.assertIn("measured only", table.render())

    def test_counterexample_traces_are_found_for_failing_partitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, trace in (("top.a", True), ("top.b", False)):
                folder = os.path.join(tmp, "strategies", name, "simple")
                os.makedirs(folder)
                if trace:
                    Path(folder, "trace.vcd").write_text("$date\n$end\n")
            found = counterexample_traces(tmp, ["top.a", "top.b", "top.c"])
            self.assertEqual(sorted(found), ["top.a"])


@unittest.skipUnless(
    os.path.isfile(tool_bin("yosys", "YOSYS")) or shutil.which(tool_bin("yosys", "YOSYS")),
    "yosys is not installed",
)
class SynthesisTest(unittest.TestCase):
    def test_ptw_sky130_matches_the_shipped_area(self):
        from reproduce import synth

        with tempfile.TemporaryDirectory() as tmp:
            code = synth.main(["ptw", "--judge", "sky130", "--rtol", "0", "--results", tmp])
        self.assertEqual(code, 0)

    def test_one_rtl_set_runs_alone(self):
        from reproduce import synth

        with tempfile.TemporaryDirectory() as tmp:
            code = synth.main(["ptw", "--rtl", "optimised", "--judge", "sky130", "--rtol", "0", "--results", tmp])
            report = json.loads(Path(tmp, "synth.json").read_text())
        self.assertEqual(code, 0)
        self.assertEqual(list(report["sky130"]["ptw"]), ["optimised"])

    def test_custom_rtl_is_measured(self):
        from reproduce import synth

        with tempfile.TemporaryDirectory() as tmp:
            mine = os.path.join(tmp, "mine")
            os.makedirs(mine)
            shutil.copy2(ROOT / "reproduce/cases/ptw/optimised_rtl/ptw.sv", mine)
            code = synth.main(["ptw", "--rtl-dir", mine, "--judge", "sky130", "--results", os.path.join(tmp, "out")])
            report = json.loads(Path(tmp, "out", "synth.json").read_text())
        self.assertEqual(code, 0)
        self.assertEqual(report["sky130"]["ptw"]["custom"]["cells"], 2470)

    def test_schematic_is_written(self):
        from chia_svapshot.tools.yosys import write_schematic

        spec = common.load_cases()["tlb"]
        with tempfile.TemporaryDirectory() as tmp:
            made = write_schematic(common.rtl_sources("tlb", "optimised", spec), "tlb", workdir=tmp, stage="rtl")
            self.assertTrue(made["ok"], made["log_tail"])
            self.assertIn("digraph", Path(made["dot"]).read_text()[:200])


if __name__ == "__main__":
    unittest.main()
