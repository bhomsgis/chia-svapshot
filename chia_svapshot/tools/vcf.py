"""VC Formal helpers: where ``vcf`` runs, the FPV batch and GUI runners, and the elaboration gate.

``vcf`` runs in one of two places, chosen by :func:`resolve_target`:

* on this machine, from ``VC_FORMAL_HOME`` (or ``VC_STATIC_HOME``), from ``PATH``, or
  from the default install folder ``~/synopsys/ins/vc_static/<version>``;
* in a running container through ``docker exec``.

``CHIA_VCF_DOCKER=<name>`` picks the container, ``CHIA_VCF_DOCKER=auto`` takes the
first running ``vcformal*`` container, and ``CHIA_VCF_DOCKER=off`` runs on this
machine. Inside a container the tool always runs there. ``VC_FORMAL_HOME_DOCKER``
names the install folder as the container sees it when that differs from the host.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

CONTAINER_MARKERS = ('/.dockerenv', '/run/.containerenv')
HOME_PATTERNS = ('~/synopsys/ins/vc_static/*', '/opt/synopsys/vc_static/*')
OFF_WORDS = ('off', 'none', 'local', 'host')
PASS_THROUGH = ('SNPSLMD_LICENSE_FILE', 'SNPS_LICENSE_FILE', 'LM_LICENSE_FILE')


def inside_container() -> bool:
    """True when this process runs inside a Docker or Podman container."""
    return any(os.path.exists(marker) for marker in CONTAINER_MARKERS)


def _has_vcf(home: str) -> bool:
    return bool(home) and os.path.isfile(os.path.join(home, 'bin', 'vcf'))


def install_homes() -> List[str]:
    """Candidate VC Formal install folders: the environment first, then the default folders."""
    homes = [os.environ.get(name, '') for name in ('VC_FORMAL_HOME', 'VC_STATIC_HOME')]
    for pattern in HOME_PATTERNS:
        homes.extend(sorted(glob.glob(os.path.expanduser(pattern)), reverse=True))
    unique: List[str] = []
    for home in homes:
        if home and home not in unique:
            unique.append(home)
    return unique


def install_home() -> str:
    """First candidate install folder that holds ``bin/vcf``, or an empty string."""
    for home in install_homes():
        if _has_vcf(home):
            return home
    return ''


def vcf_binary(default: str = 'vcf') -> str:
    """``vcf`` from the environment's install folder, from ``PATH``, or from a default folder."""
    for name in ('VC_FORMAL_HOME', 'VC_STATIC_HOME'):
        home = os.environ.get(name, '')
        if _has_vcf(home):
            return os.path.join(home, 'bin', 'vcf')
    found = shutil.which(default)
    if found:
        return found
    home = install_home()
    return os.path.join(home, 'bin', 'vcf') if home else default


@dataclass(frozen=True)
class VcfTarget:
    """Where ``vcf`` runs: this machine (``container`` empty) or a container."""

    container: str = ''
    home: str = ''

    @property
    def binary(self) -> str:
        if self.home:
            return os.path.join(self.home, 'bin', 'vcf')
        return vcf_binary()

    def describe(self) -> str:
        if self.container:
            return f'docker exec {self.container} -> {self.binary}'
        where = ' (inside a container)' if inside_container() else ''
        return f'{self.binary}{where}'


def running_containers(prefix: str = 'vcformal') -> List[str]:
    """Names of running containers that start with ``prefix``, shortest name first."""
    try:
        proc = subprocess.run(
            ['docker', 'ps', '--format', '{{.Names}}'],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    names = [name for name in proc.stdout.split() if name.startswith(prefix)]
    return sorted(names, key=lambda name: (len(name), name))


def _container_home(container: str) -> str:
    candidates = [os.environ.get('VC_FORMAL_HOME_DOCKER', '')] + install_homes()
    candidates = [c for i, c in enumerate(candidates) if c and c not in candidates[:i]]
    probe = 'for home in "$@"; do if [ -x "$home/bin/vcf" ]; then echo "$home"; exit 0; fi; done; exit 1'
    try:
        proc = subprocess.run(
            ['docker', 'exec', container, 'sh', '-c', probe, 'sh'] + candidates,
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'docker exec {container} failed: {exc}') from exc
    found = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not found:
        raise RuntimeError(
            f'container {container} offers bin/vcf under 0 of {candidates or "the candidate folders"}; '
            'set VC_FORMAL_HOME_DOCKER to the install folder as the container sees it '
            + (proc.stderr or '').strip()
        )
    return found[0]


def resolve_target(docker: Optional[str] = None, *, auto: bool = False) -> VcfTarget:
    """Choose where ``vcf`` runs.

    ``docker`` is a container name, ``auto`` (first running ``vcformal*`` container
    when this process runs on the host), or ``off`` (this machine). ``None`` reads
    ``CHIA_VCF_DOCKER``. With an empty choice, ``auto=True`` behaves as ``auto`` and
    ``auto=False`` runs on this machine.
    """
    choice = (docker if docker is not None else os.environ.get('CHIA_VCF_DOCKER', '')).strip()
    lowered = choice.lower()
    if lowered in OFF_WORDS:
        choice = ''
    elif lowered == 'auto' or (not choice and auto):
        choice = ''
        if not inside_container():
            names = running_containers()
            choice = names[0] if names else ''
    if not choice:
        binary = vcf_binary()
        home = ''
        for candidate in install_homes():
            if binary == os.path.join(candidate, 'bin', 'vcf'):
                home = candidate
        return VcfTarget('', home)
    return VcfTarget(choice, _container_home(choice))


def ensure_visible(target: VcfTarget, path: str) -> None:
    """Raise when the host folder of ``path`` is absent inside the container of ``target``."""
    if not target.container:
        return
    probe = os.path.abspath(path)
    while probe and not os.path.isdir(probe) and probe != os.path.dirname(probe):
        probe = os.path.dirname(probe)
    proc = subprocess.run(
        ['docker', 'exec', target.container, 'test', '-d', probe],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f'folder {probe} is absent inside container {target.container}; choose a --results folder '
            'under a path the container mounts (for example under $HOME)'
        )


def command(
    target: VcfTarget,
    args: List[str],
    workdir: str,
    *,
    gui: bool = False,
) -> Tuple[List[str], Optional[str], Dict[str, str]]:
    """Argument vector, working directory, and environment that run ``vcf <args>``."""
    workdir = os.path.abspath(workdir)
    env = dict(os.environ)
    if not gui:
        if env.get('TERM', 'dumb') in ('', 'dumb'):
            env['TERM'] = 'vt100'
        if not env.get('TERMINFO'):
            for candidate in ('/usr/share/terminfo', '/lib/terminfo', '/etc/terminfo'):
                if os.path.isdir(candidate):
                    env['TERMINFO'] = candidate
                    break
    if not target.container:
        if target.home:
            env.setdefault('VC_FORMAL_HOME', target.home)
            env.setdefault('VC_STATIC_HOME', target.home)
        return [target.binary] + list(args), workdir, env
    cmd = ['docker', 'exec']
    if gui:
        cmd.append('-it' if sys.stdin.isatty() else '-i')
    cmd += ['-w', workdir]
    settings = {'VC_FORMAL_HOME': target.home, 'VC_STATIC_HOME': target.home}
    if gui:
        if os.environ.get('DISPLAY'):
            settings['DISPLAY'] = os.environ['DISPLAY']
    else:
        settings['TERM'] = 'vt100'
        settings['TERMINFO'] = '/usr/share/terminfo'
    for name in PASS_THROUGH:
        if os.environ.get(name):
            settings[name] = os.environ[name]
    for name, value in settings.items():
        cmd += ['-e', f'{name}={value}']
    return cmd + [target.container, target.binary] + list(args), None, env


def run_batch(
    workdir: str,
    tcl: str,
    session: str,
    log_path: str,
    *,
    docker: Optional[str] = None,
    timeout_s: Optional[int] = None,
    target: Optional[VcfTarget] = None,
) -> int:
    """Run ``vcf -f <tcl> -batch`` inside ``workdir`` and write the console log.

    ``tcl`` and ``session`` are relative to ``workdir``. Returns the process
    return code (124 after a timeout). Output goes to ``log_path``. ``target``
    overrides ``docker`` and ``CHIA_VCF_DOCKER``.
    """
    target = target or resolve_target(docker)
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    cmd, cwd, env = command(
        target, ['-session', session, '-no_restore', '-f', tcl, '-batch'], workdir,
    )
    try:
        with open(log_path, 'w', encoding='utf-8') as log:
            proc = subprocess.run(
                cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, timeout=timeout_s,
            )
        return proc.returncode
    except subprocess.TimeoutExpired:
        return 124


def run_gui(workdir: str, tcl: str, session: str, *, target: VcfTarget) -> int:
    """Open the VC Formal GUI (``vcf -gui -f <tcl>``) and wait until the user closes it."""
    cmd, cwd, env = command(
        target, ['-gui', '-session', session, '-no_restore', '-f', tcl], workdir, gui=True,
    )
    return subprocess.run(cmd, cwd=cwd, env=env).returncode


def file_list(sources: List[str], prop: str, bind: str, workdir: str) -> str:
    """VCS file list (``files_vcf.vc``) for a DUT, its property file, and the bind file.

    ``sources`` come first in compile order (packages before users), then the
    checker module, then the ``bind`` that attaches it. Paths are absolute.
    """
    work = os.path.abspath(workdir)
    lines = [
        '+verilog2001ext+.v',
        '+verilog2001ext+.vh',
        '+systemverilogext+.sv',
        '+systemverilogext+.svh',
        '+libext+.v',
        '+libext+.sv',
        '+librescan',
        f'+incdir+{work}',
        f'-y {work}',
    ]
    lines.extend(os.path.abspath(path) for path in sources)
    lines.append(os.path.abspath(prop))
    lines.append(os.path.abspath(bind))
    return '\n'.join(lines) + '\n'


def fpv_script(
    top: str,
    clock: str,
    reset: str,
    reset_sense: str = 'low',
    *,
    coverage: bool = True,
    signoff: bool = False,
    command_time: str = '1H',
    property_time: str = '5M',
    gui: bool = False,
) -> str:
    """Tcl for one FPV run: proofs, vacuity, formal core, density, and coverage.

    The script expects ``files_vcf.vc`` next to itself and writes every report
    to ``reports/``. Each report command sits in ``catch`` so that a command
    missing from a tool version degrades one report and keeps the proof results.

    * ``properties.rpt``                   per-property proof and vacuity status
    * ``formal_core*.rpt``                 registers and inputs each proof used
    * ``assertion_density.rpt``            registers and inputs in an assertion cone
    * ``cov.rpt``                          ``report_cov`` summary of the formal-core
                                           coverage task (line, condition, FSM, toggle)

    ``coverage=False`` runs the proofs only. ``signoff=True`` adds the
    observation and formal coverage signoff step. ``gui=True`` leaves out the
    final ``exit`` so that ``vcf -gui -f`` keeps the session open.
    """
    sense = 'low' if str(reset_sense).lower().startswith('l') else 'high'
    lines = [
        f'# VC Formal FPV script for {top}',
        f'set top {top}',
        'set_fml_appmode FPV',
        'set files_vcf [file join [file dirname [info script]] files_vcf.vc]',
        'suppress_message SM_UST',
        'analyze -format sverilog -vcs "-f $files_vcf"',
        '',
        f'set collect_coverage {1 if coverage else 0}',
        'if {$collect_coverage} {',
        '    elaborate -cov all -sva $top',
        '} else {',
        '    elaborate -sva $top',
        '}',
        '',
        '# A vacuity goal per implication and a witness goal per assumption separate',
        '# a proof from a proof whose antecedent is unreachable.',
        'set_fml_var fml_vacuity_on true',
        'set_fml_var fml_witness_on true',
        '',
        f'create_clock {clock} -period 100',
        f'set_change_at -default -posedge -clock {clock}',
        f'create_reset {reset} -sense {sense}',
        '',
        'set report_dir "reports"',
        'set covdb "formal_cov"',
        'file delete -force $report_dir',
        'file delete -force $covdb.vdb $covdb.el',
        'file mkdir $report_dir',
        '',
        f'set command_time_budget "{command_time}"',
        'if {[info exists env(CHIA_FML_MAX_TIME)]} { set command_time_budget $env(CHIA_FML_MAX_TIME) }',
        f'set property_time_budget "{property_time}"',
        'if {[info exists env(CHIA_FML_PROPERTY_TIME)]} { set property_time_budget $env(CHIA_FML_PROPERTY_TIME) }',
        'catch {set_fml_var fml_max_time $command_time_budget}',
        'catch {set_fml_var fml_property_time_limit $property_time_budget}',
        '',
        '# -block waits for every result, which writes the PROP_I_RESULT lines.',
        'if {[catch {check_fv -block} check_fv_error]} {',
        '    echo "CHECK_FV_FAILED: $check_fv_error"',
        '}',
        'catch {redirect -file $report_dir/properties.rpt {report_fv -verbose}}',
        '',
        'if {$collect_coverage} {',
        '    # Formal core: the registers and inputs each proof relied on.',
        '    catch {compute_formal_core -block}',
        '    catch {redirect -file $report_dir/formal_core.rpt {report_formal_core -list}}',
        '    catch {redirect -file $report_dir/formal_core_verbose.rpt {report_formal_core -verbose -no_summary}}',
        '',
        '    # Property density: registers and primary inputs inside an assert cone.',
        '    catch {',
        '        redirect -file $report_dir/assertion_density.rpt {',
        '            echo "DENSITY_SCOPE reg"',
        '            catch {report_assertion_density -list -status all -type reg}',
        '            echo "DENSITY_SCOPE pi"',
        '            catch {report_assertion_density -list -status all -type pi}',
        '        }',
        '    }',
        '',
        '    # Map the formal core onto the instrumented coverage goals. -structural',
        '    # marks the goals inside the core and its cone of influence as covered',
        '    # by structure in one pass.',
        '    catch {compute_formal_core_coverage -par_task FPV -structural -block}',
        '    catch {save_formal_core_results -db_name $covdb.vdb -elfile_name $covdb.el -mark fcore_plus_coi}',
        '    catch {redirect -file $report_dir/cov.rpt {report_cov}}',
    ]
    if signoff:
        lines += [
            '',
            '    # Observation (OC) and formal (FC) coverage signoff.',
            '    catch {signoff_config -type all}',
            '    catch {signoff_config -type {oc fc}}',
            '    catch {check_fv -block}',
            '    catch {check_fv -block -type {oc fc}}',
            '    catch {redirect -file $report_dir/fv_coverage.rpt {report_fv_coverage}}',
            '    foreach kind {observe cover structural line toggle condition branch assert assume} {',
            '        catch {redirect -file $report_dir/cov_${kind}.rpt {report_cov -type $kind}}',
            '    }',
            '    catch {redirect -file $report_dir/cov_signoff.rpt {report_cov}}',
        ]
    lines += ['}']
    if gui:
        lines += ['echo "FPV run finished; the GUI stays open"', '']
    else:
        lines += ['exit', '']
    return '\n'.join(lines)


def _ok(text: str, returncode: int) -> bool:
    return (
        returncode == 0
        and 'Error:[' not in text
        and 'Error-[' not in text
        and ('0 error(s)' in text or 'Elaborat' in text)
    )


def _pvalue_flags(top: str, parameters: Optional[Dict[str, object]]) -> str:
    """VCS ``-pvalue`` flags for simple numeric / based-literal overrides."""
    if not parameters:
        return ''
    try:
        from instantiation_params import format_vcs_pvalue_string

        flags = format_vcs_pvalue_string(
            top, {str(k): str(v) for k, v in parameters.items()})
    except Exception:
        flags = ''
        parts = []
        for name, value in parameters.items():
            text = str(value).strip()
            if text.isdigit():
                parts.append(f'-pvalue+{top}.{name}={text}')
        flags = ' '.join(parts)
    return flags


def elaborate(
    sources: List[str],
    top: str,
    *,
    workdir: Optional[str] = None,
    vcf_bin: str = 'vcf',
    include_dirs: Optional[List[str]] = None,
    parameters: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Return ok=True when Verdi KDB reports 0 errors.

    Set ``CHIA_VCF_DOCKER`` (container name) to run ``vcf`` via
    ``docker exec`` when the host shell lacks a working ``vcf``.
    """
    target = resolve_target()
    # docker exec cannot chdir into host /tmp; keep the session under $HOME.
    if workdir is None and target.container:
        cache = os.path.join(os.path.expanduser('~'), '.cache', 'vcf_elab')
        os.makedirs(cache, exist_ok=True)
        workdir = tempfile.mkdtemp(prefix='vcf_elab_', dir=cache)
    td_cm = tempfile.TemporaryDirectory(prefix='vcf_elab_') if workdir is None else None
    root = workdir or td_cm.name
    if workdir is not None:
        os.makedirs(root, exist_ok=True)
    try:
        tcl_path = os.path.join(root, 'elab.tcl')
        rel_sources = []
        for src in sources:
            dest = os.path.join(root, os.path.basename(src))
            if os.path.abspath(src) != os.path.abspath(dest):
                try:
                    if not os.path.exists(dest):
                        os.symlink(os.path.abspath(src), dest)
                except OSError:
                    import shutil
                    shutil.copy2(src, dest)
            rel_sources.append(os.path.basename(src))
        inc_flags = []
        for include in include_dirs or []:
            dest = os.path.join(root, 'include')
            if os.path.isdir(include):
                import shutil
                os.makedirs(dest, exist_ok=True)
                for child in os.listdir(include):
                    origin = os.path.join(include, child)
                    target = os.path.join(dest, child)
                    if os.path.isdir(origin):
                        if os.path.exists(target):
                            shutil.copytree(origin, target, dirs_exist_ok=True)
                        else:
                            shutil.copytree(origin, target)
                    else:
                        shutil.copy2(origin, target)
                inc_flags.append('+incdir+include')
        vcs_inc = f'-vcs "{" ".join(inc_flags)}"' if inc_flags else ''
        pvalues = _pvalue_flags(top, parameters)
        with open(tcl_path, 'w', encoding='utf-8') as handle:
            handle.write('set_fml_appmode FPV\n')
            for src in rel_sources:
                if vcs_inc:
                    handle.write(f'analyze -format sverilog {vcs_inc} {src}\n')
                else:
                    handle.write(f'analyze -format sverilog {src}\n')
            if pvalues:
                handle.write(f'elaborate {top} -vcs {{{pvalues}}}\nexit\n')
            else:
                handle.write(f'elaborate {top}\nexit\n')
        cmd, cwd, env = command(
            target, ['-session', 'vcst_rtdb', '-no_restore', '-f', 'elab.tcl', '-batch'], root,
        )
        if not target.container and not target.home:
            cmd[0] = vcf_binary(vcf_bin)
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
        text = (proc.stdout or '') + '\n' + (proc.stderr or '')
        with open(os.path.join(root, 'vcf_elab.log'), 'w', encoding='utf-8') as log:
            log.write(text)
        ok = _ok(text, proc.returncode)
        if target.container:
            ok = ok or (proc.returncode == 0 and 'Error:[' not in text and 'Error-[' not in text)
        return {
            'ok': ok,
            'returncode': proc.returncode,
            'log_tail': text[-2500:],
        }
    finally:
        if td_cm is not None:
            td_cm.cleanup()
