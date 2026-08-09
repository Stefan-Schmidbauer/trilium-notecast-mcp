#!/usr/bin/env bash
#
# Deploy the Trilium MCP server from a dev machine to a server.
#
# The image is built by the *remote* Docker daemon over an SSH context, so no
# registry and no source checkout on the server are involved. The compose
# service definition and the secrets stay on the server, owned by the admin
# user — this script only replaces the image and recreates the container.
#
# Configure it per machine in deploy.env (gitignored, see deploy/deploy.env.example)
# or by exporting the same variables. There are no built-in host defaults.
#
# Usage:  ./deploy.sh [--no-build]
#
set -euo pipefail

cd "$(dirname "$0")"

# Per-machine settings; environment wins over the file.
[ -f deploy.env ] && { set -a; . ./deploy.env; set +a; }

DEPLOY_HOST=${DEPLOY_HOST:-}
DOCKER_CTX=${DOCKER_CTX:-trilium-notecast-mcp}
REMOTE_COMPOSE=${REMOTE_COMPOSE:-/opt/docker/docker-compose.yml}
SERVICE=trilium-notecast-mcp
IMAGE=trilium-notecast-mcp
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:9151/healthz}

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ -n "$DEPLOY_HOST" ] \
    || die "DEPLOY_HOST is not set. Copy deploy/deploy.env.example to deploy.env and fill it in, or export DEPLOY_HOST=user@host."

# --- version tag -------------------------------------------------------------
if git rev-parse --git-dir >/dev/null 2>&1; then
    # Named after the last release tag: v1.2.0 on the tag itself, and
    # v1.2.0-3-gafe0bf9 three commits past it. That makes the rollback command
    # in the README refer to a version someone can place, rather than to a bare
    # commit SHA. --always falls back to the short SHA before the first tag
    # exists, so this works in a repo that has never been released. All three
    # forms are valid Docker tags.
    TAG=$(git describe --tags --always)
    if ! git diff --quiet HEAD -- .; then
        TAG="${TAG}-dirty"
        info "Working tree has uncommitted changes — tagging as ${TAG}"
    fi
else
    TAG=$(date +%Y%m%d-%H%M%S)
fi

# --- docker context ----------------------------------------------------------
if ! docker context inspect "$DOCKER_CTX" >/dev/null 2>&1; then
    info "Creating docker context '${DOCKER_CTX}' -> ssh://${DEPLOY_HOST}"
    docker context create "$DOCKER_CTX" --docker "host=ssh://${DEPLOY_HOST}"
fi

docker --context "$DOCKER_CTX" version >/dev/null 2>&1 \
    || die "Cannot reach the Docker daemon via context '${DOCKER_CTX}'. Check SSH access to ${DEPLOY_HOST}."

# --- build -------------------------------------------------------------------
if [ "${1:-}" != "--no-build" ]; then
    info "Building ${IMAGE}:${TAG} on ${DEPLOY_HOST}"
    # Context is this directory; it is streamed to the remote daemon over SSH.
    # Both tags point at the same image: :local is what compose references,
    # :$TAG stays behind so a previous build can be re-pinned for a rollback.
    docker --context "$DOCKER_CTX" build \
        -t "${IMAGE}:${TAG}" \
        -t "${IMAGE}:local" \
        .
fi

# --- recreate ----------------------------------------------------------------
# The strings below are assembled locally and run by a shell on the server, so
# every interpolated value is single-quoted. They come from deploy.env, not from
# anything untrusted — the quoting is here so that a path with a space stays one
# argument, and so this file is not a template for doing it unquoted elsewhere.
info "Recreating ${SERVICE} via ${REMOTE_COMPOSE}"
ssh "$DEPLOY_HOST" "docker compose -f '${REMOTE_COMPOSE}' up -d '${SERVICE}'"

# --- verify ------------------------------------------------------------------
info "Waiting for the server to answer"
for i in $(seq 1 15); do
    if ssh "$DEPLOY_HOST" "curl -sf -o /dev/null '${HEALTH_URL}'"; then
        info "Healthy — deployed ${IMAGE}:${TAG}"
        exit 0
    fi
    sleep 1
done

printf '\033[1;31mERROR:\033[0m server did not become healthy. Recent logs:\n' >&2
ssh "$DEPLOY_HOST" "docker logs --tail 30 '${SERVICE}'" >&2
exit 1
