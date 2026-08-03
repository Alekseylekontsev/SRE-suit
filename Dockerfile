# SRE Agent — batteries-included, hardened runtime image.
#
# Security posture:
#   * slim base, pinned by tag (pin by digest in CI for full supply-chain control)
#   * runs as a non-root user
#   * NO secrets or config baked in — config.yaml is mounted read-only at runtime
#   * encrypted secret store writes to a dedicated, owned, writable volume
#   * works under `--read-only` root filesystem (only the data volume is writable)
FROM python:3.13-slim AS build

# Build the wheel in an isolated venv so the runtime image carries only what it needs.
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml ./
COPY sre_agent ./sre_agent
# "all tools": core (cryptography) + yaml + tracing + reasoning extras.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install ".[yaml,tracing,reasoning]"

# ---------------------------------------------------------------- runtime
FROM python:3.13-slim AS runtime

LABEL org.opencontainers.image.title="sre-agent" \
      org.opencontainers.image.description="Universal SRE coordinator (Proxmox + Hetzner) with human-in-the-loop approvals" \
      org.opencontainers.image.source="https://github.com/Alekseylekontsev/SRE-suit"

# Non-root user; data dir for the encrypted secret store / audit exports.
RUN useradd --system --uid 10001 --create-home --home-dir /home/sre sre \
 && mkdir -p /var/lib/sre-agent /config \
 && chown -R sre:sre /var/lib/sre-agent

COPY --from=build /opt/venv /opt/venv
COPY docker/entrypoint.sh /usr/local/bin/sre-entrypoint
RUN chmod 0755 /usr/local/bin/sre-entrypoint

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Mount your config.yaml here (read-only); the entrypoint passes it as --config.
    SRE_CONFIG_PATH=/config/config.yaml

WORKDIR /var/lib/sre-agent
USER sre

# `sre-agent health` is a cheap read-only reachability probe.
HEALTHCHECK --interval=60s --timeout=15s --start-period=10s --retries=3 \
  CMD ["/usr/local/bin/sre-entrypoint", "health"]

ENTRYPOINT ["/usr/local/bin/sre-entrypoint"]
CMD ["--help"]
