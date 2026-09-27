PYTHON ?= python

# Size budgets (MB) — see CLAUDE.md
BASE_GLB_MAX_MB := 25
DIST_MAX_MB := 60

.PHONY: fetch candidates build pack serve renders-check

fetch:
	$(PYTHON) scripts/fetch_base.py

candidates:
	$(PYTHON) scripts/fetch_candidates.py

build:
	$(PYTHON) scripts/build_proposals.py
	$(MAKE) pack

# Check dist/ against the size budgets; fails the build if either is exceeded.
pack:
	@mkdir -p dist
	@if [ -f dist/base.glb ]; then \
		size=$$(stat -c %s dist/base.glb); \
		echo "dist/base.glb: $$((size / 1048576)) MB (limit $(BASE_GLB_MAX_MB) MB)"; \
		if [ $$size -ge $$(($(BASE_GLB_MAX_MB) * 1048576)) ]; then echo "ERROR: dist/base.glb is over budget"; exit 1; fi; \
	else \
		echo "WARNING: dist/base.glb not found"; \
	fi
	@total=$$(du -sb dist | cut -f1); \
	echo "dist/ total: $$((total / 1048576)) MB (limit $(DIST_MAX_MB) MB)"; \
	if [ $$total -ge $$(($(DIST_MAX_MB) * 1048576)) ]; then echo "ERROR: dist/ is over budget"; exit 1; fi

serve:
	@echo "Serving repo root on port 8000; open /web/ in the forwarded 'skyline' port"
	$(PYTHON) -m http.server 8000

# List renders/ with the date each file was last committed (or its mtime if uncommitted).
renders-check:
	@files=$$(find renders -type f ! -name .gitkeep 2>/dev/null | sort); \
	if [ -z "$$files" ]; then echo "renders/ is empty"; exit 0; fi; \
	printf '%-24s %6s  %s\n' "DATE" "SIZE" "FILE"; \
	for f in $$files; do \
		d=$$(git log -1 --format=%cs -- "$$f" 2>/dev/null); \
		[ -n "$$d" ] || d="$$(date -r "$$f" +%F) (uncommitted)"; \
		printf '%-24s %6s  %s\n' "$$d" "$$(du -h "$$f" | cut -f1)" "$$f"; \
	done
