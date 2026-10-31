#!/usr/bin/env bash
# Run one pytest suite inside the test image and copy its JUnit + coverage reports to ./reports.
# Usage: run-tests.sh <test-image> <suite-name> <pytest paths/args...>
# docker cp (not a bind mount) is used because Jenkins runs in a container next to the Docker engine:
# the engine cannot see paths inside the Jenkins workspace.
set -euo pipefail
image="$1"; suite="$2"; shift 2
name="tt-test-${BUILD_TAG:-local}-${suite}"
name="${name//[^A-Za-z0-9_.-]/-}"
mkdir -p reports
docker rm -f "$name" >/dev/null 2>&1 || true

rc=0
docker run --name "$name" -e TT_SOLVER_WORKERS="${TT_SOLVER_WORKERS:-4}" "$image" \
  python -m pytest "$@" -p no:cacheprovider \
    --junitxml="/tmp/reports/junit-${suite}.xml" -o junit_suite_name="${suite}" \
    --cov=timetable --cov-report=term --cov-report="xml:/tmp/reports/coverage-${suite}.xml" \
  || rc=$?

docker cp "$name:/tmp/reports/." reports/ || echo "no reports produced by suite ${suite}"
docker rm -f "$name" >/dev/null
exit "$rc"
