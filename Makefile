# Shortcuts for the reproduction scripts. Pass flags with ARGS, for example:
#   make fpv ARGS="ptw --rtl optimised --gui"
PY   ?= python3
ARGS ?=

.PHONY: help tools synth lec fpv test

help:
	@echo "make tools   install the OSS CAD Suite (Yosys, EQY) into toolchains/"
	@echo "make synth   Sky130 and generic synthesis      (reproduce/synth.py)"
	@echo "make lec     Yosys EQY equivalence checks      (reproduce/lec.py)"
	@echo "make fpv     VC Formal proofs and coverage     (reproduce/fpv.py)"
	@echo "make test    unit tests"

tools:
	scripts/install_oss_cad_suite.sh

synth:
	$(PY) reproduce/synth.py $(ARGS)

lec:
	$(PY) reproduce/lec.py $(ARGS)

fpv:
	$(PY) reproduce/fpv.py $(ARGS)

test:
	$(PY) -m pytest tests -q
