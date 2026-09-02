.PHONY: build package publish clean test

PACKAGE_NAME := riscos-impression
VERSION ?= $(shell ./ci-vars --json | python3 -c 'import json, sys; print(json.load(sys.stdin)["CI_PROJECT_VERSION"])')
WHEEL_VERSION ?= $(shell python3 -c 'import re, sys; parts = sys.argv[1].split("."); numbers = []; [numbers.append(parts.pop(0)) for _ in range(len(parts)) if parts and parts[0].isdigit()]; base = ".".join(numbers) or "0"; suffix = ".".join(parts); print(base + (("+" + re.sub(r"[^a-zA-Z0-9]+", ".", suffix).strip(".")) if suffix else ""))' '$(VERSION)')
BUILD_SOURCE := build/source
PACKAGE_DIR := build/$(PACKAGE_NAME)_$(VERSION)_all
PACKAGE_FILE := dist/$(PACKAGE_NAME)_$(VERSION)_all.deb

build:
	rm -rf "$(BUILD_SOURCE)" dist
	mkdir -p "$(BUILD_SOURCE)/src"
	cp -a README.md LICENSE "$(BUILD_SOURCE)/"
	cp -a src/riscos_impression "$(BUILD_SOURCE)/src/riscos_impression"
	sed 's/^version = ".*"/version = "$(WHEEL_VERSION)"/' pyproject.toml > "$(BUILD_SOURCE)/pyproject.toml"
	python3 -m build --outdir "$(CURDIR)/dist" "$(BUILD_SOURCE)"

package:
	$(MAKE) clean build
	mkdir -p "$(PACKAGE_DIR)/DEBIAN" \
		"$(PACKAGE_DIR)/usr/share/doc/$(PACKAGE_NAME)"
	python3 -m pip install --root "$(PACKAGE_DIR)" --prefix /usr \
		--no-deps --no-compile --ignore-installed dist/*.whl
	cp README.md LICENSE "$(PACKAGE_DIR)/usr/share/doc/$(PACKAGE_NAME)/"
	printf '%s\n' \
		'Package: $(PACKAGE_NAME)' \
		'Version: $(VERSION)' \
		'Section: utils' \
		'Priority: optional' \
		'Architecture: all' \
		'Maintainer: Charles Ferguson <gerph@gerph.org>' \
		'Depends: python3 (>= 3.10)' \
		'Description: Decode RISC OS Impression documents' \
		' Decode RISC OS Impression documents and convert them to OvationPro' \
		' DDL, PDF, and HTML.' \
		> "$(PACKAGE_DIR)/DEBIAN/control"
	mkdir -p dist
	dpkg-deb --build --root-owner-group "$(PACKAGE_DIR)" "$(PACKAGE_FILE)"

publish: build
	python3 -m twine upload dist/*

clean:
	rm -rf dist/ build/ *.egg-info

test:
	python3 -m pytest
