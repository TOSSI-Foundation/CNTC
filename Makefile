# CNTC — Cloud Native Telecom Certification Framework — one entrypoint over the automation.
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
.PHONY: help prereqs doctor configure run run-conformance run-perf run-n3neg verdict certify \
        dashboard dashboard-bg dashboard-stop profiles lint test k8s-deploy k8s-run k8s-clean clean

help:  ## show targets
> @echo "CNTC automation — targets:"
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

# --- run ----------------------------------------------------------------------
run:  ## full e2e: all + n3neg -> merge -> verdict -> certify  (CONFIG= CAMPAIGN=)
> ./scripts/cntc-run-all.sh $(CONFIG) $(CAMPAIGN)

run-conformance:  ## pfcp + n3neg only (the certification set) + grade
> python3 -m upfbench.cli run --config $(CONFIG) --suite conformance --campaign $(CAMPAIGN)
> python3 -m cntc.cli verdict campaigns/$(CAMPAIGN)/results.json --write-back

run-perf:  ## performance + load + pfcp only
> python3 -m upfbench.cli run --config $(CONFIG) --suite all --campaign $(CAMPAIGN)

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

test:  ## run the verdict-engine unit tests
> python3 tests/test_verdict.py

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
