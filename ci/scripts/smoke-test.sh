#!/usr/bin/env bash
# Smoke test of the runtime image: the container must become healthy (Docker HEALTHCHECK), run as a
# non-root user, serve the API and the UI, and generate a real timetable (IT programme, offline LLM).
# Usage: smoke-test.sh <image>
set -euo pipefail
image="$1"
name="tt-smoke-${BUILD_TAG:-local}"
name="${name//[^A-Za-z0-9_.-]/-}"

cleanup() {
  echo "--- container log (tail) ---"
  docker logs --tail 30 "$name" 2>&1 || true
  docker rm -f "$name" >/dev/null 2>&1 || true
}
docker rm -f "$name" >/dev/null 2>&1 || true
trap cleanup EXIT

docker run -d --name "$name" --health-interval=2s --health-start-period=5s \
  -e TT_AUTOPILOT=off -e TT_LLM=offline -e TT_SOLVER_WORKERS="${TT_SOLVER_WORKERS:-4}" "$image" >/dev/null

status=starting
for _ in $(seq 1 60); do
  status="$(docker inspect --format '{{.State.Health.Status}}' "$name")"
  [ "$status" = healthy ] && break
  [ "$status" = unhealthy ] && break
  sleep 2
done
echo "container health: $status"
[ "$status" = healthy ] || { echo "container did not become healthy"; exit 1; }

uid="$(docker exec "$name" id -u)"
echo "container user id: $uid"
[ "$uid" != 0 ] || { echo "container runs as root"; exit 1; }

docker exec -i "$name" python - <<'PY'
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if "json" in r.headers.get("Content-Type", "") else raw)


status, health = call("/api/health")
assert status == 200 and health["status"] == "ok" and health["data"], health
print("health:", health)

_, spec = call("/api/agents")
assert len(spec["agents"]) == 9 and all(len(a["tools"]) <= 5 for a in spec["agents"]), "agent spec"
print("agents:", ", ".join(a["name"] for a in spec["agents"]))

_, datasets = call("/api/datasets")
main = next(d for d in datasets if d["id"] == "input.xlsx")
assert main["valid"] and main["groups"] == 270, main
print(f"dataset input.xlsx: {main['groups']} groups, {main['rooms']} rooms")

_, page = call("/")
assert b"<html" in page.lower(), "UI index.html"
print("UI: index.html served")

status, run = call("/api/runs", {"dataset": "input.xlsx", "programs": ["IT"], "llm": "offline",
                                 "solver_seconds": 8, "name": "smoke"})
assert status == 201, run
deadline = time.time() + 240
while time.time() < deadline:
    _, st = call(f"/api/runs/{run['id']}")
    if st["status"] not in ("running", "cancelling"):
        break
    time.sleep(2)
assert st["status"] == "ok", st
_, det = call(f"/api/runs/{run['id']}/details")
hard = det["validation"]["hard_violations"]
assert hard == 0, det["validation"]
print(f"timetable run {run['id']}: status={st['status']}, hard violations={hard}, "
      f"max agent share={st['result']['load']['max_share']:.0%}, {st['elapsed_s']:.1f} s")
print("SMOKE TEST PASSED")
PY
