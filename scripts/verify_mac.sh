#!/usr/bin/env bash
# Day 7 on your Mac: run the sensor consumer and the weather CronJob on a local kind
# cluster, then check them. Safe to rerun: every step reuses what already exists.
#
#   make up-stream                  # first: Postgres, Kafka and Redis in Docker Compose
#   bash scripts/verify_mac.sh      # build, load, deploy, check
#   bash scripts/verify_mac.sh --clean   # also delete the kind cluster at the end
#
# Needs: OrbStack (or Docker Desktop) running, kind, kubectl, uv.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="agri/sensor-consumer:0.1.0"
CLUSTER="agri"
NS="agri"
PF_PORT=18000

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok() { printf '\033[32mOK\033[0m   %s\n' "$*"; }
fail() { printf '\033[31mFAIL\033[0m %s\n' "$*"; exit 1; }

step "1/7 Tools"
for tool in docker kind kubectl uv; do
  command -v "$tool" >/dev/null || fail "$tool is not installed"
done
ok "docker, kind, kubectl and uv found"

step "2/7 Services the pods will call (Docker Compose)"
running="$(docker compose --profile stream ps --status running --services)"
for svc in postgres kafka redis; do
  grep -qx "$svc" <<<"$running" || fail "$svc is not running: run 'make up-stream' first"
done
ok "postgres, kafka and redis are running"

step "3/7 Cluster"
if kind get clusters | grep -qx "$CLUSTER"; then
  ok "kind cluster '$CLUSTER' already exists"
else
  kind create cluster --config k8s/kind-config.yaml
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

step "4/7 Image"
docker build -q -f ingest/Dockerfile -t "$IMAGE" .
kind load docker-image "$IMAGE" --name "$CLUSTER"  # no registry: copy it into the node
ok "built and loaded $IMAGE"

step "5/7 Deploy"
kubectl apply -f k8s/namespace.yaml >/dev/null
# The DSN is a secret: created here from a literal, never stored in git.
kubectl -n "$NS" create secret generic agri-db \
  --from-literal=AGRI_DSN=postgresql://agri:agri@host.docker.internal:5432/agri \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -k k8s/
kubectl -n "$NS" rollout status deployment/sensor-consumer --timeout=180s \
  || { kubectl -n "$NS" describe pods -l app=sensor-consumer | tail -30; fail "rollout did not finish"; }
ok "2 replicas ready (readiness = joined the consumer group)"

step "6/7 Checks"
pods="$(kubectl -n "$NS" get pods -l app=sensor-consumer -o name)"
for pod in $pods; do
  if kubectl -n "$NS" logs "$pod" | grep -q "now also reading partitions \[[0-9]"; then
    ok "$pod owns partitions: $(kubectl -n "$NS" logs "$pod" | grep -o 'partitions \[[0-9, ]*\]' | tail -1)"
  else
    fail "$pod has no partitions yet (check: kubectl -n $NS logs $pod)"
  fi
done

kubectl -n "$NS" port-forward svc/sensor-consumer "$PF_PORT:8000" >/dev/null 2>&1 &
pf=$!
trap 'kill $pf 2>/dev/null || true' EXIT
sleep 3
before="$(curl -s "localhost:$PF_PORT/metrics" | awk '/^messages_processed_total/ {s += $2} END {print s + 0}')"
uv run python -m ingest.sensor_producer --rounds 5 --interval 0.5 >/dev/null
sleep 8
after="$(curl -s "localhost:$PF_PORT/metrics" | awk '/^messages_processed_total/ {s += $2} END {print s + 0}')"
# A Service port-forward reaches ONE pod, so it sees only that pod's share of the 100.
[ "${after%.*}" -gt "${before%.*}" ] && ok "metrics move: messages_processed_total $before -> $after (one pod's share)" \
  || fail "messages_processed_total did not increase ($before -> $after)"

kubectl -n "$NS" delete job weather-verify --ignore-not-found >/dev/null
kubectl -n "$NS" create job --from=cronjob/weather-ingest weather-verify >/dev/null
if kubectl -n "$NS" wait --for=condition=complete job/weather-verify --timeout=180s >/dev/null; then
  ok "weather CronJob ran: $(kubectl -n "$NS" logs job/weather-verify | tail -1)"
else
  kubectl -n "$NS" logs job/weather-verify | tail -20
  fail "weather job did not complete"
fi

step "7/7 Summary"
kubectl -n "$NS" get pods,cronjob
echo
echo "Everything checks out. Explore with: kubectl -n $NS get all, kubectl -n $NS logs -l app=sensor-consumer"
if [[ "${1:-}" == "--clean" ]]; then
  kind delete cluster --name "$CLUSTER"
  ok "cluster deleted"
fi
