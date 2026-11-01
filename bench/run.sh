#!/bin/sh
# Wrapper for the benchmark service. Run it from the repo root (Git Bash on Windows, or any POSIX shell):
#
#   sh bench/run.sh --label NAME [--cases id,...] [--include-writes] [--throughput] [--expect-fraction F]
#                   [--runs N --warmup N] [--replace]
#   sh bench/run.sh explain --label NAME [--cases id,...] [--runs N --warmup N]     (index on and off, task 4.1b)
#
# It collects the host facts the container cannot see, passes them as BENCH_* variables, and runs the bench service
# as the host user so writes to the bind-mounted bench/results work on Linux as well as Docker Desktop.
# No password is read or passed here: Compose takes them from .env.
set -eu

MSYS_NO_PATHCONV=1
export MSYS_NO_PATHCONV

cd "$(dirname "$0")/.."
mkdir -p bench/results

HOST_UID=$(id -u)
HOST_GID=$(id -g)
export HOST_UID HOST_GID

commit=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain -- . ':!bench/results')" ]; then
    commit="${commit}-dirty"
fi

pg_cid=$(docker compose ps -q postgres)
mongo_cid=$(docker compose ps -q mongodb)
if [ -z "$pg_cid" ] || [ -z "$mongo_cid" ]; then
    echo "run.sh: the postgres and mongodb containers are not running (docker compose up -d --wait web)" >&2
    exit 1
fi

app_image=$(docker image inspect --format '{{.Id}}' neighboar-app:${NEIGHBOAR_IMAGE_TAG:-local})
pg_image=$(docker inspect --format '{{.Image}}' "$pg_cid")
mongo_image=$(docker inspect --format '{{.Image}}' "$mongo_cid")

# The WiredTiger cache size as the running server was started with it, not as .env says.
mongo_cache=$(docker inspect --format '{{range .Args}}{{println .}}{{end}}' "$mongo_cid" |
    sed -n 's/^--wiredTigerCacheSizeGB=//p' | tr -d '\r')

pg_mem=$(docker inspect --format '{{.HostConfig.Memory}}' "$pg_cid")
mongo_mem=$(docker inspect --format '{{.HostConfig.Memory}}' "$mongo_cid")

# Power: a laptop on battery or in a power-saving profile runs slower by varying amounts, so time only on mains.
# Linux reads /sys and power-profiles-daemon; elsewhere (Docker Desktop on Windows) both are recorded as unknown.
power_source=unknown
for supply in /sys/class/power_supply/*; do
    [ "$(cat "$supply/type" 2>/dev/null)" = "Mains" ] || continue
    if [ "$(cat "$supply/online" 2>/dev/null)" = "1" ]; then
        power_source=mains
    elif [ "$power_source" = unknown ]; then
        power_source=battery
    fi
done
power_profile=$(powerprofilesctl get 2>/dev/null || echo unknown)
if [ "$power_source" = battery ]; then
    echo "run.sh: the machine is on battery; plug it in before timing (see bench/README.md, step 0)" >&2
    exit 2
fi

BENCH_GIT_COMMIT=$commit
BENCH_IMAGES=$(printf '{"app": "%s", "postgres": "%s", "mongodb": "%s"}' "$app_image" "$pg_image" "$mongo_image")
BENCH_DOCKER_MEM=$(docker info --format '{{.MemTotal}}' | tr -d '\r')
BENCH_MONGO_CACHE=$mongo_cache
BENCH_MEM_LIMITS=$(printf '{"postgres": %s, "mongodb": %s}' "$pg_mem" "$mongo_mem")
BENCH_POWER=$(printf '{"source": "%s", "profile": "%s"}' "$power_source" "$power_profile")
export BENCH_GIT_COMMIT BENCH_IMAGES BENCH_DOCKER_MEM BENCH_MONGO_CACHE BENCH_MEM_LIMITS BENCH_POWER

module=bench.run
if [ "${1:-}" = "explain" ]; then
    module=bench.explain
    shift
fi

exec docker compose run --rm bench python -m "$module" "$@"
