#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MANIFEST_TEMPLATE="$SCRIPT_DIR/kubernetes/network-benchmark.yaml"
LOG_DIR="$SCRIPT_DIR/logs"

CLIENT_NODE=""
SERVER_NODE=""
IMAGE="quay.io/nwadekar/kubernetes-cluster-bench:latest"
NAMESPACE=""
DURATION=10
PARALLEL_STREAMS=1
PROTOCOL="tcp"
BANDWIDTH="1G"
TIMEOUT=120
KEEP_RESOURCES=false
RUN_ID="run-$(date -u +%Y%m%d%H%M%S)-$$"

usage() {
  cat <<'EOF'
Usage:
  ./run-benchmark.sh --client-node NODE --server-node NODE [options]

Required:
  --client-node NODE       Kubernetes node for the client pod
  --server-node NODE       Kubernetes node for the server pod

Options:
  --image IMAGE            Benchmark image (default: quay.io/nwadekar/kubernetes-cluster-bench:latest)
  --namespace NAMESPACE    Kubernetes namespace (default: current context namespace)
  --duration SECONDS       Test duration (default: 10)
  --parallel STREAMS       Number of parallel iperf3 streams (default: 1)
  --protocol tcp|udp       Transport protocol (default: tcp)
  --bandwidth RATE         UDP target rate, such as 1G or 500M (default: 1G)
  --timeout SECONDS        Kubernetes wait timeout (default: 120)
  --run-id ID              Identifier used in pod and log names
  --keep-resources         Keep benchmark pods and service after completion
  -h, --help               Show this help
EOF
}

fail() {
  echo "error: $*" >&2
  exit 2
}

require_value() {
  [ "$#" -ge 2 ] || fail "missing value for $1"
  [ -n "$2" ] || fail "empty value for $1"
  case "$2" in
    -*) fail "missing value for $1" ;;
  esac
}

is_positive_integer() {
  case "$1" in
    ''|*[!0-9]*|0) return 1 ;;
    *) return 0 ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --client-node)
      require_value "$@"
      CLIENT_NODE=$2
      shift 2
      ;;
    --server-node)
      require_value "$@"
      SERVER_NODE=$2
      shift 2
      ;;
    --image)
      require_value "$@"
      IMAGE=$2
      shift 2
      ;;
    --namespace)
      require_value "$@"
      NAMESPACE=$2
      shift 2
      ;;
    --duration)
      require_value "$@"
      DURATION=$2
      shift 2
      ;;
    --parallel)
      require_value "$@"
      PARALLEL_STREAMS=$2
      shift 2
      ;;
    --protocol)
      require_value "$@"
      PROTOCOL=$2
      shift 2
      ;;
    --bandwidth)
      require_value "$@"
      BANDWIDTH=$2
      shift 2
      ;;
    --timeout)
      require_value "$@"
      TIMEOUT=$2
      shift 2
      ;;
    --run-id)
      require_value "$@"
      RUN_ID=$2
      shift 2
      ;;
    --keep-resources)
      KEEP_RESOURCES=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[ -n "$CLIENT_NODE" ] || fail "--client-node is required"
[ -n "$SERVER_NODE" ] || fail "--server-node is required"
[ "$CLIENT_NODE" != "$SERVER_NODE" ] || fail "client and server nodes must be different"
[ -f "$MANIFEST_TEMPLATE" ] || fail "manifest not found: $MANIFEST_TEMPLATE"
command -v kubectl >/dev/null 2>&1 || fail "kubectl is required"

if [ -z "$NAMESPACE" ]; then
  NAMESPACE=$(kubectl config view --minify --output='jsonpath={.contexts[0].context.namespace}' 2>/dev/null || true)
  NAMESPACE=${NAMESPACE:-default}
fi

case "$PROTOCOL" in
  tcp|udp) ;;
  *) fail "--protocol must be tcp or udp" ;;
esac

is_positive_integer "$DURATION" || fail "--duration must be a positive integer"
is_positive_integer "$PARALLEL_STREAMS" || fail "--parallel must be a positive integer"
is_positive_integer "$TIMEOUT" || fail "--timeout must be a positive integer"

# These values are substituted into Kubernetes names or YAML scalar values.
case "$CLIENT_NODE$SERVER_NODE" in
  *[!a-z0-9.-]*) fail "node names may contain only lowercase letters, numbers, '.', or '-'" ;;
esac
case "$NAMESPACE" in
  [a-z0-9]*) ;;
  *) fail "--namespace must start with a lowercase letter or number" ;;
esac
case "$NAMESPACE" in
  *[a-z0-9]) ;;
  *) fail "--namespace must end with a lowercase letter or number" ;;
esac
case "$NAMESPACE" in
  *[!a-z0-9-]*) fail "--namespace may contain only lowercase letters, numbers, or '-'" ;;
esac
case "$RUN_ID" in
  [a-z0-9]*) ;;
  *) fail "--run-id must start with a lowercase letter or number" ;;
esac
case "$RUN_ID" in
  *[a-z0-9]) ;;
  *) fail "--run-id must end with a lowercase letter or number" ;;
esac
case "$RUN_ID" in
  *[!a-z0-9-]*) fail "--run-id may contain only lowercase letters, numbers, or '-'" ;;
esac
[ "${#RUN_ID}" -le 38 ] || fail "--run-id must be 38 characters or fewer"
case "$IMAGE" in
  ''|*[!A-Za-z0-9._/@:-]*) fail "--image contains unsupported characters" ;;
esac
case "$BANDWIDTH" in
  ''|*[!0-9KMG]*) fail "--bandwidth must look like 1G, 500M, or 100K" ;;
esac

mkdir -p "$LOG_DIR"
SERVER_SERVICE="network-benchmark-server-$RUN_ID"
SERVER_POD="network-benchmark-server-$RUN_ID"
CLIENT_POD="network-benchmark-client-$RUN_ID"
SERVER_HOST="$SERVER_SERVICE"
CLIENT_LOG="$LOG_DIR/${RUN_ID}-client.json"
SERVER_LOG="$LOG_DIR/${RUN_ID}-server.log"
METADATA_LOG="$LOG_DIR/${RUN_ID}-metadata.txt"
RENDERED_MANIFEST=$(mktemp "${TMPDIR:-/tmp}/iperf3-benchmark.XXXXXX.yaml")
APPLY_STARTED=false

cleanup() {
  if [ "$KEEP_RESOURCES" = false ] && [ "$APPLY_STARTED" = true ]; then
    if ! kubectl delete -n "$NAMESPACE" -f "$RENDERED_MANIFEST" --ignore-not-found >/dev/null 2>&1; then
      echo "warning: failed to clean up Kubernetes resources for run $RUN_ID" >&2
    fi
  fi
  rm -f "$RENDERED_MANIFEST"
}
trap cleanup EXIT

sed \
  -e "s|__RUN_ID__|$RUN_ID|g" \
  -e "s|__CLIENT_NODE__|$CLIENT_NODE|g" \
  -e "s|__SERVER_NODE__|$SERVER_NODE|g" \
  -e "s|__IMAGE__|$IMAGE|g" \
  -e "s|__DURATION__|$DURATION|g" \
  -e "s|__PARALLEL_STREAMS__|$PARALLEL_STREAMS|g" \
  -e "s|__PROTOCOL__|$PROTOCOL|g" \
  -e "s|__BANDWIDTH__|$BANDWIDTH|g" \
  "$MANIFEST_TEMPLATE" > "$RENDERED_MANIFEST"

cat > "$METADATA_LOG" <<EOF
run_id=$RUN_ID
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
client_node=$CLIENT_NODE
server_node=$SERVER_NODE
namespace=$NAMESPACE
image=$IMAGE
protocol=$PROTOCOL
duration_seconds=$DURATION
parallel_streams=$PARALLEL_STREAMS
udp_bandwidth=$BANDWIDTH
EOF

for resource in "service/$SERVER_SERVICE" "pod/$SERVER_POD" "pod/$CLIENT_POD"; do
  if kubectl get -n "$NAMESPACE" "$resource" >/dev/null 2>&1; then
    fail "resource already exists: $resource; choose a different --run-id"
  fi
done

APPLY_STARTED=true
kubectl apply -n "$NAMESPACE" -f "$RENDERED_MANIFEST"
kubectl wait -n "$NAMESPACE" --for=jsonpath='{.status.phase}'=Running "pod/$SERVER_POD" --timeout="${TIMEOUT}s"
kubectl wait -n "$NAMESPACE" --for=jsonpath='{.status.phase}'=Running "pod/$CLIENT_POD" --timeout="${TIMEOUT}s"

# The manifest only starts long-running containers. Run the selected benchmark inside them.
kubectl exec -n "$NAMESPACE" "$SERVER_POD" -- sh -c \
  'iperf3 -s --one-off > /tmp/network-benchmark-server.log 2>&1 &' 
sleep 2

CLIENT_COMMAND="iperf3 -c $SERVER_HOST -J -t $DURATION -P $PARALLEL_STREAMS --get-server-output"
if [ "$PROTOCOL" = "udp" ]; then
  CLIENT_COMMAND="$CLIENT_COMMAND -u -b $BANDWIDTH"
fi

set +e
kubectl exec -n "$NAMESPACE" "$CLIENT_POD" -- sh -c "$CLIENT_COMMAND" > "$CLIENT_LOG" 2>&1
CLIENT_STATUS=$?
set -e

kubectl exec -n "$NAMESPACE" "$SERVER_POD" -- cat /tmp/network-benchmark-server.log > "$SERVER_LOG" 2>&1 || true
printf 'finished_at=%s\nclient_status=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CLIENT_STATUS" >> "$METADATA_LOG"

echo "Client result: $CLIENT_LOG"
echo "Server log:     $SERVER_LOG"
echo "Metadata:       $METADATA_LOG"

if [ "$CLIENT_STATUS" -ne 0 ]; then
  echo "benchmark failed; client output was saved to $CLIENT_LOG" >&2
  exit 1
fi
