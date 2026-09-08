# CNTC: Cloud Native Telecom Certification Framework, one entrypoint over the automation.
#
#   make                     # help
#   make prereqs             # install deps + build pfcpsim + doctor  (sudo)
#   make configure           # interactive wizard -> configs/<campaign>.yaml
#   make run CONFIG=configs/my-upf.yaml CAMPAIGN=SDCORE-AF-001
#   make dashboard           # live web UI  (or: make dashboard-bg)
#   make k8s-deploy          # live dashboard in kubernetes
#
# Override vars on the command line, e.g.  make run CONFIG=... CAMPAIGN=...
.RECIPEPREFIX = >
SHELL := /bin/bash

CONFIG    ?= configs/my-upf.yaml
CAMPAIGN  ?= SDCORE-AF-001
DASH_PORT ?= 8050
export KUBECONFIG ?= $(HOME)/.kube/config
export PATH := $(PATH):/var/lib/rancher/rke2/bin:$(HOME)/.local/bin

.DEFAULT_GOAL := help
.PHONY: help prereqs doctor configure cp-configure cp-doctor cp-run cp-certify \
        ran-prereqs ran-configure ran-list ran-doctor ran-run ran-certify run run-conformance run-perf run-n3neg \
        eupf-run eupf-certify verdict certify \
        dashboard dashboard-bg dashboard-stop profiles lint test k8s-deploy k8s-run k8s-clean clean

help:  ## show targets
> @echo "CNTC automation, targets:"
> @grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | sort \
>   | awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-18s\033[0m %s\n",$$1,$$2}'
> @echo ""
> @echo "vars:  CONFIG=$(CONFIG)  CAMPAIGN=$(CAMPAIGN)  DASH_PORT=$(DASH_PORT)"

# --- setup --------------------------------------------------------------------
prereqs:  ## install deps + build pfcpsim + run doctor (needs sudo)
> sudo ./scripts/cntc-prereqs.sh

doctor:  ## preflight readiness check
> python3 -m upfbench.cli doctor

configure:  ## interactive wizard -> configs/<campaign>.yaml
> ./scripts/cntc-configure.py

# --- control plane (cpbench) ---------------------------------------------------
cp-configure:  ## interactive wizard -> control-plane campaign config (docker or k8s)
> ./scripts/cpbench-configure.py

cp-doctor:  ## preflight the control-plane rig  (CONFIG=)
> python3 -m cpbench.cli doctor --config $(CONFIG)

cp-run:  ## run one NF or all  (CONFIG= NF=all|amf|smf|nrf|ausf|udm|udr|pcf CAMPAIGN=)
> python3 -m cpbench.cli run --config $(CONFIG) --nf $(or $(NF),all) $(if $(CAMPAIGN),--campaign $(CAMPAIGN),)

cp-certify:  ## issue a control-plane certificate  (CAMPAIGN= NF=amf|smf|nrf|ausf|udm|udr|pcf)
> python3 -m cntc.cli certify campaigns/$(CAMPAIGN)/results.json --profile $(or $(NF),amf)-conformance

# --- RAN (ranbench) ------------------------------------------------------------
ran-prereqs:  ## install the RAN tester: deps + the OAI UE simulator (needs sudo)
> ./scripts/bootstrap_ranbench.sh

ran-configure:  ## wizard -> RAN campaign config (derives the UE radio params from the O-DU)
> ./scripts/ranbench-configure.py

ran-list:  ## list the split-gNB product classes + their catalogs
> python3 -m ranbench.cli list

ran-doctor:  ## preflight the RAN rig  (CONFIG=configs/ocudu-ran.yaml)
> python3 -m ranbench.cli doctor --config $(CONFIG)

ran-run:  ## run the RAN suite  (CONFIG= TARGET=all|cucp|cuup|du CAMPAIGN=)
> python3 -m ranbench.cli run --config $(CONFIG) --target $(or $(TARGET),all) $(if $(CAMPAIGN),--campaign $(CAMPAIGN),)

ran-certify:  ## issue a RAN certificate  (CAMPAIGN= TARGET=cucp|cuup|du)
> python3 -m cntc.cli certify campaigns/$(CAMPAIGN)/results.json --profile $(or $(TARGET),cucp)-conformance

# --- run ----------------------------------------------------------------------
run:  ## full e2e: all + n3neg -> merge -> verdict -> certify  (CONFIG= CAMPAIGN=)
> ./scripts/cntc-run-all.sh $(CONFIG) $(CAMPAIGN)

run-conformance:  ## pfcp + n3neg only (the certification set) + grade
> python3 -m upfbench.cli run --config $(CONFIG) --suite conformance --campaign $(CAMPAIGN)
> python3 -m cntc.cli verdict campaigns/$(CAMPAIGN)/results.json --write-back

run-perf:  ## performance + load + pfcp only
> python3 -m upfbench.cli run --config $(CONFIG) --suite all --campaign $(CAMPAIGN)

# --- free5GC + eUPF (eBPF/XDP): dual certificate --------------------------------
eupf-run:  ## eUPF: run one suite  (CONFIG=configs/eupf.yaml SUITE=conformance|ebpf CAMPAIGN=)
> sudo python3 -m upfbench.cli run --config $(or $(CONFIG),configs/eupf.yaml) --suite $(or $(SUITE),conformance) --profile $(if $(filter ebpf,$(SUITE)),upf-ebpf,conformance) --campaign $(or $(CAMPAIGN),EUPF)

eupf-certify:  ## eUPF: BOTH certs (conformance + eBPF/XDP) end-to-end  (CONFIG= PREFIX=)
> sudo ./scripts/eupf-certify.sh $(or $(CONFIG),configs/eupf.yaml) $(or $(PREFIX),EUPF)

run-n3neg:  ## N3 robustness only (crashes+recovers the UPF)
> python3 -m upfbench.cli run --config $(CONFIG) --suite n3neg --campaign $(CAMPAIGN)-N3

verdict:  ## (re)grade a campaign -> scorecard  (CAMPAIGN=)
> python3 -m cntc.cli verdict campaigns/$(CAMPAIGN)/results.json --write-back

certify:  ## issue a certificate if the verdict is PASS  (CAMPAIGN=)
> python3 -m cntc.cli certify campaigns/$(CAMPAIGN)/results.json

# --- dashboard ----------------------------------------------------------------
dashboard:  ## launch the live dashboard (foreground; Ctrl-C to stop)
> UPFBENCH_DASH_PORT=$(DASH_PORT) UPFBENCH_DASH_LIVE_MS=15000 python3 -m dashboard.app

dashboard-bg:  ## launch the dashboard detached in tmux (session cntc-dash)
> tmux kill-session -t cntc-dash 2>/dev/null || true
> tmux new-session -d -s cntc-dash 'UPFBENCH_DASH_PORT=$(DASH_PORT) UPFBENCH_DASH_LIVE_MS=15000 python3 -m dashboard.app'
> @echo "dashboard: http://$$(hostname -I 2>/dev/null | awk '{print $$1}'):$(DASH_PORT)   (tmux: cntc-dash)"

dashboard-stop:  ## stop the tmux dashboard
> tmux kill-session -t cntc-dash 2>/dev/null || true
> @echo "stopped"

# --- utilities ----------------------------------------------------------------
profiles:  ## list CNTC requirement profiles
> python3 -m cntc.cli profiles

lint:  ## validate the requirement catalogs
> python3 -m cntc.cli lint

test:  ## run the unit tests (verdict engine, catalogs, smoke)
> @if python3 -c "import pytest" 2>/dev/null; then python3 -m pytest -q tests/; \
>  else echo "pytest not installed, running the verdict engine tests only"; python3 tests/test_verdict.py; fi

# --- kubernetes ---------------------------------------------------------------
k8s-deploy:  ## deploy the live dashboard in kubernetes (edit deploy/k8s first)
> kubectl apply -f deploy/k8s/cntc-dashboard.yaml

k8s-run:  ## run the whole suite in-cluster as a Job
> kubectl apply -f deploy/k8s/cntc-testrunner-job.yaml

k8s-clean:  ## remove the k8s dashboard + job
> kubectl delete -f deploy/k8s/cntc-dashboard.yaml --ignore-not-found
> kubectl delete -f deploy/k8s/cntc-testrunner-job.yaml --ignore-not-found

clean:  ## remove python caches
> find . -name __pycache__ -type d -not -path './third_party/*' -exec rm -rf {} + 2>/dev/null || true
> @echo "cleaned"
