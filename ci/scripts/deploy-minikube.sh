#!/usr/bin/env bash
# CD: deploy an image built by this pipeline to the local Minikube cluster and verify the rollout.
# Usage: deploy-minikube.sh <image:tag>      (KUBECONFIG = jenkins-deployer ServiceAccount, namespace timetable)
# 1. copy the image into the Minikube node (no registry needed for the local cluster)
# 2. kubectl apply -k with an overlay that pins the image tag of this build
# 3. wait for the rollout, then call /api/health through the NodePort service
set -euo pipefail
image="$1"
repo="${image%:*}"
tag="${image##*:}"
node="${MINIKUBE_NODE:-minikube}"
: "${KUBECONFIG:?KUBECONFIG is not set}"

echo "Loading ${image} into the Minikube node '${node}'"
docker save "$image" | docker exec -i "$node" docker load

mkdir -p build/k8s
cat > build/k8s/kustomization.yaml <<YAML
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../k8s/app
images:
  - name: multi-agent-timetable
    newName: ${repo}
    newTag: "${tag}"
YAML

kubectl version --output=yaml | grep -E 'gitVersion' || true
kubectl apply -k build/k8s
kubectl -n timetable annotate deployment/timetable --overwrite \
  kubernetes.io/change-cause="Jenkins ${JOB_NAME:-local} #${BUILD_NUMBER:-0}: ${image} (${GIT_COMMIT:-unknown})"
kubectl -n timetable rollout status deployment/timetable --timeout=240s

server="$(kubectl config view --minify --output 'jsonpath={.clusters[0].cluster.server}')"
host="${server#https://}"; host="${host%%:*}"
port="$(kubectl -n timetable get service timetable --output 'jsonpath={.spec.ports[0].nodePort}')"
echo "Checking http://${host}:${port}/api/health"
curl --fail --silent --show-error --retry 15 --retry-delay 2 --retry-all-errors "http://${host}:${port}/api/health"
echo
kubectl -n timetable get deployment,replicaset,pod,service,pvc --output wide
kubectl -n timetable rollout history deployment/timetable | tail -n 5
echo "DEPLOYED ${image} -> http://${host}:${port}"
