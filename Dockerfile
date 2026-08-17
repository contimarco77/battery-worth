# battery-worth as a container: the analysis CLI, with no Python setup required.
#
# Multi-stage, both stages on the same pinned base. Pinning them together is not
# tidiness — the wheel is built against the builder's interpreter and run on the
# runtime's, so a version skew between the two would be an ABI mismatch that only
# appears at import time, on a user's machine.
#
#   docker build -t battery-worth .
#   docker run --rm battery-worth                       # help
#   docker run --rm -v "$PWD:/data" -w /data battery-worth analyze energy.csv ...
#
# 3.11 matches `requires-python = ">=3.11"` and ruff's `target-version = "py311"`,
# so the image runs the same interpreter the lint rules assume.

# --------------------------------------------------------------------------
# Builder: build the wheel, install it into a self-contained venv
# --------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# A venv rather than the system site-packages: the runtime stage copies one
# directory and gets the package, its dependencies and its console scripts,
# with nothing from the build left behind to copy accidentally.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# Only what the wheel is built from. `src/` last, because it is what changes
# most often — the metadata layers above it stay cached across code edits.
COPY pyproject.toml README.md ./
COPY src/ ./src/

# `--only-binary=:all:` is the load-bearing flag, not an optimization. pandas,
# numpy and matplotlib all publish manylinux cp311 wheels, so nothing here needs
# a compiler; if a dependency ever resolves to an sdist instead, this fails the
# build with the offending package named rather than silently adding minutes to
# it and then failing at runtime for want of a toolchain that was never installed.
# hatchling itself is exempt — it is a pure-Python build backend, and the wheel it
# produces is what the constraint is really about.
RUN pip install --no-cache-dir hatchling \
    && python -m hatchling build --target wheel \
    && pip install --no-cache-dir --only-binary=:all: dist/*.whl \
    && rm -rf dist

# --------------------------------------------------------------------------
# Runtime: the venv and an interpreter. No pip, no build tooling, no apt.
# --------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

# The repository URL appears here, in `battery_worth.REPO_URL` and in
# `[project.urls]`. All three are pinned together by
# `test_packaging_metadata_matches_the_printed_url`, which reads this file: a
# third unpinned copy is exactly the drift that test exists to prevent.
LABEL org.opencontainers.image.source="https://github.com/contimarco77/battery-worth"
LABEL org.opencontainers.image.description="Would a home battery have paid off for YOU? Retrospective what-if analysis from your real energy data."
LABEL org.opencontainers.image.licenses="Apache-2.0"

ENV PATH="/opt/venv/bin:$PATH" \
    # Container stdout is always a pipe, which makes it block-buffered while
    # stderr stays unbuffered — so progress output lands *after* the error that
    # stopped it. This project already fixed that bug once (the 2026-08-12
    # session log); the default would reproduce it for every containerized run.
    PYTHONUNBUFFERED=1 \
    # matplotlib writes a font cache on first use and warns on stderr when it
    # cannot. That warning is raised outside the CLI's `warnings.catch_warnings`
    # block, so it bypasses the report's WARNINGS section entirely and appears as
    # unexplained noise beside the results.
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY --from=builder /opt/venv /opt/venv

# The Home Assistant export step, which produces this tool's input. It is
# deliberately not part of the package — the analysis engine has no network or
# auth surface — so it is not in the wheel and would otherwise be missing from
# the image, leaving a Home Assistant user unable to produce the CSV the image
# analyses. Carried as a sibling entrypoint, not promoted into the package:
#     docker run --rm --entrypoint ha-export battery-worth --help
# Standard library only, so it runs on the venv's interpreter with no extra deps.
COPY scripts/ha_export.py /usr/local/bin/ha-export
RUN chmod 0755 /usr/local/bin/ha-export \
    && sed -i '1s|.*|#!/opt/venv/bin/python|' /usr/local/bin/ha-export

# Non-root by default. `--user 1000:1000` is a UID, not a name, so the account
# exists only to give the default run a home and a non-zero UID.
RUN useradd --create-home --uid 1000 battery

# MPLCONFIGDIR must be writable by whoever runs the image, and that is not
# knowable at build time: a host user mounting an output volume will pass
# `--user $(id -u):$(id -g)` to avoid root-owned files, and that UID has no
# entry in /etc/passwd. Mode 1777 (like /tmp) makes the directory writable for
# any UID while the sticky bit stops one from deleting another's cache.
RUN mkdir -p /tmp/matplotlib && chmod 1777 /tmp/matplotlib

# A default working directory to mount over: `-v "$PWD:/data"` then makes
# relative paths in the command line resolve the way the user typed them.
RUN mkdir -p /data && chmod 1777 /data
WORKDIR /data

USER 1000:1000

# CMD, not part of ENTRYPOINT, so that a bare `docker run battery-worth` prints
# help while any argument list replaces it: `docker run battery-worth analyze ...`.
ENTRYPOINT ["battery-worth"]
CMD ["--help"]
