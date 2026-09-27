PYTHON ?= python3

# Size budgets (MB) — see CLAUDE.md
BASE_GLB_MAX_MB := 25
DIST_MAX_MB := 60

.PHONY: all fetch candidates build pack site serve renders-check

# Full path: `make fetch build serve` (or `make all` for fetch + build).
all: fetch build

# Base city: footprints, heights, terrain, land use -> dist/base.glb, terrain.*, landuse.json.
# Downloads are cached in data/raw/. Options: make fetch REFRESH=1  |  make fetch SKIP=city,overpass
fetch:
	$(PYTHON) scripts/fetch_base.py $(if $(REFRESH),--refresh) $(if $(SKIP),--skip $(SKIP))

candidates:
	$(PYTHON) scripts/fetch_candidates.py

# proposals.csv + proposals.geojson -> dist/proposals.json; data/cameras.json -> dist/cameras.json
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

# Assemble the site exactly as GitHub Pages serves it: web/* at the root,
# dist/ and renders/ beside it. The Pages workflow runs this same target.
site:
	rm -rf site
	mkdir -p site
	cp -r web/. site/
	for d in dist renders; do [ -d "$$d" ] && cp -r "$$d" site/ || true; done
	find site -name .gitkeep -delete

serve: site
	@echo "Serving site/ on port 8000 (forwarded as 'skyline'); rerun make serve after edits"
	$(PYTHON) -m http.server 8000 --directory site

# List renders/ and flag any older than the current data date (max last_checked).
renders-check:
	$(PYTHON) scripts/renders_check.py
