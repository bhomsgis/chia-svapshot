# Reproduce the results

Three scripts reproduce the numbers of the paper on the RTL that ships in `reproduce/cases/`.
Each script prints a table of expected values against measured values, writes a JSON file to
`results/`, and exits with 0 when every value matches.

| Script | Tool | What it reproduces | Typical run time |
|---|---|---|---|
| `synth.py` | Yosys + Sky130 | standard-cell area and generic gate counts | about 1 minute for all cases |
| `lec.py` | Yosys EQY | partition verdicts of original against optimised RTL | tlb: over 10 minutes (the partition step alone takes about 7); div_unit and ptw are longer |
| `fpv.py` | VC Formal | proofs, property density, formal core, `report_cov` totals | 40 to 80 seconds per run (6 runs for all cases) |

## Cases

| Case | Module | RTL files | Retained assertions |
|---|---|---|---|
| `div_unit` | integer divider | 5 | 30 |
| `ptw` | page-table walker | 5 | 43 |
| `tlb` | translation lookaside buffer | 3 | 14 |

Each case holds `original_rtl/`, `optimised_rtl/` (the LLM rewrite that the loop kept), `sva/` (the
retained SVApshot properties) and `bind/` (the bind file that attaches them).

## Requirements

* Python 3.9 or newer; the scripts use the standard library.
* OSS CAD Suite for synthesis and LEC: `scripts/install_oss_cad_suite.sh` unpacks it into
  `toolchains/oss-cad-suite`. The variables `YOSYS` and `EQY` select another install.
* VC Formal X-2025.06-SP2 and its license for FPV (see "Where VC Formal runs").

## Commands

Run every command from the repository root.

```bash
# Synthesis
python3 reproduce/synth.py                         # three cases, both RTL sets, both judges
python3 reproduce/synth.py ptw --judge sky130      # one case, one judge

# Equivalence checking (detach long runs)
python3 reproduce/lec.py tlb --jobs 2
setsid nohup python3 reproduce/lec.py div_unit --jobs 2 > lec_div_unit.log 2>&1 &

# Formal property verification
python3 reproduce/fpv.py                           # three cases, original and optimised RTL
python3 reproduce/fpv.py ptw --rtl optimised       # one case, one RTL set
```

`make synth`, `make lec` and `make fpv` run the same scripts; pass flags with
`make fpv ARGS="ptw --rtl optimised"`.

## Selecting the RTL

All three scripts accept the same two flags.

| Flag | Meaning |
|---|---|
| `--rtl original` | the original RTL |
| `--rtl optimised` | the shipped LLM rewrite |
| `--rtl custom` | your RTL from `--rtl-dir` |
| `--rtl both` | original and optimised (default for `synth.py` and `fpv.py`) |
| `--rtl all` | original, optimised and custom |
| `--rtl-dir DIR` | folder with your RTL; `--rtl-dir` alone selects `--rtl custom` |

`--which` is an alias of `--rtl`. `lec.py` takes `optimised` (default), `original` or `custom`; its gold
side is always the original RTL, so `--rtl original` is an identity check.

Layout of `DIR`: for one case put the files directly in `DIR`; for several cases use `DIR/<case>/`.
The folder holds files named like the shipped sources (`ptw.sv`, `tlb.sv`, ...). The top-level file
`<top>.sv` is required; every other file that is missing comes from the original RTL. The scripts
print which file came from where. Custom runs list measured values only, and the exit code is 0 unless a tool
run fails.

```bash
python3 reproduce/synth.py ptw --rtl-dir ~/my_rtl/ptw             # area of your ptw
python3 reproduce/lec.py ptw --rtl-dir ~/my_rtl/ptw --jobs 2      # equivalence with the original
python3 reproduce/fpv.py ptw --rtl all --rtl-dir ~/my_rtl/ptw     # shipped properties on three RTL sets
```

## Graphical views

| Flag | Opens | Needs |
|---|---|---|
| `fpv.py --gui` | the VC Formal GUI (Verdi) on the staged run | an X display; one case and one RTL set |
| `synth.py --gui` | the word-level schematic in `xdot`; `--gui mapped` shows the Sky130 netlist | an X display; one case and one RTL set |
| `lec.py --gui` | the counterexample waveform of a FAIL partition in GTKWave (`--viewer surfer` for Surfer) | an X display; one case; `--partition NAME` picks the partition |

```bash
python3 reproduce/fpv.py ptw --rtl optimised --gui
python3 reproduce/synth.py tlb --rtl optimised --gui
python3 reproduce/lec.py tlb --analyse-only --gui       # reuse an earlier LEC run
```

The FPV GUI session runs the same Tcl as the batch run, so the proofs and reports exist when the window
opens. The script prints the comparison table after you close the GUI.

## Where VC Formal runs

`fpv.py` chooses the place in this order:

1. `--docker NAME` (or `CHIA_VCF_DOCKER=NAME`): `docker exec NAME vcf ...`.
2. A shell inside a container: `vcf` runs there.
3. A host shell with a running `vcformal*` container: `docker exec` into the first one.
4. `VC_FORMAL_HOME/bin/vcf`, `vcf` on `PATH`, or `~/synopsys/ins/vc_static/<version>/bin/vcf`.

`--no-docker` selects the host. The script prints the chosen command line (`vcf: ...`) before the first
run. The container must see the `--results` folder at the same path as the host; a folder under `$HOME`
works with the usual `vcformal` launcher. `VC_FORMAL_HOME_DOCKER` names the install folder as the
container sees it. The license comes from `SNPSLMD_LICENSE_FILE`; a container that already holds the
variable keeps its value.

## What the numbers mean

* **Proved non-vacuous**: assertions with a proof whose antecedent is reachable. A vacuous proof holds
  because its condition stays false in every reachable state; the vacuity goals of VC Formal separate
  the two.
* **Structural cone of influence (COI)**: the share of design signals (ports and registers) that feed at
  least one retained assertion, computed by static analysis of the RTL text. It is 1 when every signal lies
  in an assertion cone. Example: 78 of 80 signals gives 0.975 for `tlb`.
* **Property density** `[covered, total]`: registers (or primary inputs) inside the cone of at least one
  assertion, counted by VC Formal's `report_assertion_density`.
* **Formal core** `[covered, total]`: registers (or inputs) that the proofs used, counted by
  `report_formal_core`. A smaller core than the density shows that proofs need fewer signals than their
  cones contain.
* **`report_cov` totals** `[covered, total]`: line, condition, FSM state, FSM transition and toggle goals
  that the structural formal-core coverage marks as covered. The totals depend on the RTL, so the original
  and the optimised RTL carry separate reference values.
* **EQY partitions**: EQY splits the two designs into one partition per output, register and memory bit.
  PASS means equivalent up to the SAT depth bound (10), FAIL means a counterexample exists, UNKNOWN means
  the solver ended with an unknown result.
* **Sky130 area**: `abc` maps the design to `sky130_fd_sc_hd` (tt, 25 C, 1.8 V) and `stat -liberty`
  sums the cell areas in um^2. The relative change is `(optimised - original) / original`.

## Expected values

Synthesis (Sky130 area in um^2, `reproduce/expected/synth.json`):

| Case | Original | Optimised | Change |
|---|---|---|---|
| `div_unit` | 73052.5632 | 56658.0896 | -22.44% |
| `ptw` | 24611.104 | 23601.3856 | -4.10% |
| `tlb` | 65062.4 | 60077.6192 | -7.66% |

FPV (`reproduce/expected/fpv.json`): all retained assertions prove non-vacuously on both RTL sets
(30, 43 and 14). Density, formal core and the `report_cov` totals sit in the same file.

LEC (`reproduce/expected/lec.json`): the file lists the FAIL and UNKNOWN partitions per case.

`--rtol` of `synth.py` sets the relative tolerance (default 0.5%); `--rtol 0` demands exact equality.

## Result files and exit codes

| File | Content |
|---|---|
| `results/synth.json` | per judge, case and RTL set: cells, wires, area or levels |
| `results/lec.json` | per case: verdict, PASS count, FAIL and UNKNOWN partitions |
| `results/fpv.json` | per case and RTL set: proofs, density, formal core, `report_cov` |
| `results/fpv/<case>_<set>/` | staged inputs, `vcf.log` and `reports/` of each run |

| Exit code | Meaning |
|---|---|
| 0 | every compared value matches (or a custom run finished) |
| 1 | at least one value differs |
| 2 | `fpv.py` found `vcf` missing, or `--results` is absent inside the container |
| 3 | `fpv.py`: the VC Formal license checkout failed |
| 4 | `lec.py`: EQY ended before its DONE line; rerun the same command to resume |

## Tips

* EQY solvers use about 1.3 GB each. Use `--jobs 2` on a 16 GB machine; `lec.py` resumes after a killed
  solver with half the jobs.
* Run long LEC jobs detached (`setsid nohup ...`) so that a closed terminal leaves them running.
* `--analyse-only` re-reads earlier run folders of `results/` and compares them with the reference.
* `--no-coverage` runs the FPV proofs only; `--signoff` adds the observation and formal coverage step.
