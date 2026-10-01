#!/usr/bin/env bash
set -euo pipefail

if [ -t 1 ] || [ -t 2 ]; then
  RED=$'\033[0;31m'
  GREEN=$'\033[0;32m'
  YELLOW=$'\033[0;33m'
  CYAN=$'\033[0;36m'
  RESET=$'\033[0m'
else
  RED=""
  GREEN=""
  YELLOW=""
  CYAN=""
  RESET=""
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MANIFEST_TEMPLATE="$SCRIPT_DIR/network-benchmark.yaml"
LOG_DIR="$SCRIPT_DIR/logs"

CLIENT_NODE=""
SERVER_NODE=""
IMAGE="quay.io/nwadekar/kubernetes-cluster-bench:latest"
NAMESPACE=""
DURATION=10
DURATION_SET=false
TRANSFER_SIZE=""
PARALLEL_STREAMS=1
TIMEOUT=120
KEEP_RESOURCES=false
RUN_ID="run-$(date -u +%Y%m%d-%H%M%S)-$$"

usage() {
  cat <<'EOF'

Usage:
  ./run-benchmark.sh --client NODE --server NODE [options]

Required:
  -c, --client NODE        Kubernetes node for the client pod
  -s, --server NODE        Kubernetes node for the server pod

Options:
  --image IMAGE            Benchmark image (default: quay.io/nwadekar/kubernetes-cluster-bench:latest)
  -n, --namespace NAMESPACE Kubernetes namespace (default: current context namespace)
  --duration SECONDS       Test duration (default: 10; ignored with --transfer-size)
  --transfer-size SIZE     Total data to send, such as 1G or 500M
  --parallel STREAMS       Number of parallel iperf3 streams (default: 1)
  --timeout SECONDS        Kubernetes wait timeout (default: 120)
  --run-id ID              Identifier used in pod and log names
  --keep-resources         Keep benchmark pods and service after completion
  -h, --help               Show this help
EOF
}

fail() {
  printf '%s\n' "${RED}ERROR:${RESET} $*" >&2
  exit 2
}

argument_error() {
  printf '%s\n' "${RED}ERROR:${RESET} $*" >&2
  usage >&2
  exit 2
}

require_value() {
  [ "$#" -ge 2 ] || argument_error "missing value for $1"
  [ -n "$2" ] || argument_error "empty value for $1"
  case "$2" in
    -*) argument_error "missing value for $1" ;;
  esac
}

is_positive_integer() {
  case "$1" in
    ''|*[!0-9]*|0) return 1 ;;
    *) return 0 ;;
  esac
}

print_progress() {
  local percent=$1
  local width=30
  local filled=$((percent * width / 100))
  local empty=$((width - filled))
  local filled_bar
  local empty_bar

  filled_bar=$(printf '%*s' "$filled" '' | tr ' ' '#')
  empty_bar=$(printf '%*s' "$empty" '' | tr ' ' '.')
  printf '\r%sClient benchmark progress: [%s%s] %3d%%%s' \
    "$CYAN" "$filled_bar" "$empty_bar" "$percent" "$RESET"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -c|--client)
      require_value "$@"
      CLIENT_NODE=$2
      shift 2
      ;;
    -s|--server)
      require_value "$@"
      SERVER_NODE=$2
      shift 2
      ;;
    --image)
      require_value "$@"
      IMAGE=$2
      shift 2
      ;;
    -n|--namespace)
      require_value "$@"
      NAMESPACE=$2
      shift 2
      ;;
    --duration)
      require_value "$@"
      DURATION=$2
      DURATION_SET=true
      shift 2
      ;;
    --transfer-size)
      require_value "$@"
      TRANSFER_SIZE=$2
      shift 2
      ;;
    --parallel)
      require_value "$@"
      PARALLEL_STREAMS=$2
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
      argument_error "unknown argument: $1"
      ;;
  esac
done

[ -n "$CLIENT_NODE" ] || argument_error "--client is required"
[ -n "$SERVER_NODE" ] || argument_error "--server is required"
[ "$CLIENT_NODE" != "$SERVER_NODE" ] || argument_error "client and server nodes must be different"
[ -f "$MANIFEST_TEMPLATE" ] || fail "manifest not found: $MANIFEST_TEMPLATE"
command -v kubectl >/dev/null 2>&1 || fail "kubectl is required"

KUBE_CONTEXT=$(kubectl config current-context 2>/dev/null || true)
KUBE_CONTEXT=${KUBE_CONTEXT:-unknown}

if [ -z "$NAMESPACE" ]; then
  NAMESPACE=$(kubectl config view --minify --output='jsonpath={.contexts[0].context.namespace}' 2>/dev/null || true)
  NAMESPACE=${NAMESPACE:-default}
fi

is_positive_integer "$DURATION" || argument_error "--duration must be a positive integer"
is_positive_integer "$PARALLEL_STREAMS" || argument_error "--parallel must be a positive integer"
is_positive_integer "$TIMEOUT" || argument_error "--timeout must be a positive integer"
if [ "$DURATION_SET" = true ] && [ -n "$TRANSFER_SIZE" ]; then
  argument_error "--duration and --transfer-size cannot be used together"
fi

case "$TRANSFER_SIZE" in
  '') ;;
  *[!0-9KMGTP]*) argument_error "--transfer-size must look like 1G, 500M, or 100K" ;;
  *[!KMGTP])
    is_positive_integer "$TRANSFER_SIZE" || argument_error "--transfer-size must be a positive size"
    ;;
  *)
    size_number=${TRANSFER_SIZE%?}
    is_positive_integer "$size_number" || argument_error "--transfer-size must be a positive size"
    ;;
esac

# These values are substituted into Kubernetes names or YAML scalar values.
case "$CLIENT_NODE$SERVER_NODE" in
  *[!a-z0-9.-]*) argument_error "node names may contain only lowercase letters, numbers, '.', or '-'" ;;
esac
case "$NAMESPACE" in
  [a-z0-9]*) ;;
  *) argument_error "--namespace must start with a lowercase letter or number" ;;
esac
case "$NAMESPACE" in
  *[a-z0-9]) ;;
  *) argument_error "--namespace must end with a lowercase letter or number" ;;
esac
case "$NAMESPACE" in
  *[!a-z0-9-]*) argument_error "--namespace may contain only lowercase letters, numbers, or '-'" ;;
esac
case "$RUN_ID" in
  [a-z0-9]*) ;;
  *) argument_error "--run-id must start with a lowercase letter or number" ;;
esac
case "$RUN_ID" in
  *[a-z0-9]) ;;
  *) argument_error "--run-id must end with a lowercase letter or number" ;;
esac
case "$RUN_ID" in
  *[!a-z0-9-]*) argument_error "--run-id may contain only lowercase letters, numbers, or '-'" ;;
esac
[ "${#RUN_ID}" -le 38 ] || argument_error "--run-id must be 38 characters or fewer"
case "$IMAGE" in
  ''|*[!A-Za-z0-9._/@:-]*) argument_error "--image contains unsupported characters" ;;
esac

if [ "$DURATION_SET" = true ]; then
  DURATION_DISPLAY="$DURATION seconds"
  TRANSFER_SIZE_DISPLAY="N/A"
else
  DURATION_DISPLAY="N/A"
  TRANSFER_SIZE_DISPLAY="$TRANSFER_SIZE"
fi

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
SERVER_EXEC_PID=""
CLIENT_EXEC_PID=""

cleanup() {
  local cleanup_started cleanup_finished cleanup_duration

  if [ -n "$CLIENT_EXEC_PID" ]; then
    kill "$CLIENT_EXEC_PID" 2>/dev/null || true
    wait "$CLIENT_EXEC_PID" 2>/dev/null || true
    CLIENT_EXEC_PID=""
  fi

  if [ -n "$SERVER_EXEC_PID" ]; then
    printf '%s\n' "${CYAN}Stopping server benchmark session${RESET}"
    kill "$SERVER_EXEC_PID" 2>/dev/null || true
    wait "$SERVER_EXEC_PID" 2>/dev/null || true
    SERVER_EXEC_PID=""
    printf '%s\n' "${CYAN}Server benchmark session stopped${RESET}"
  fi

  if [ "$KEEP_RESOURCES" = false ] && [ "$APPLY_STARTED" = true ]; then
    printf '%s\n' "${CYAN}Cleaning up benchmark resources${RESET}"
    cleanup_started=$(date +%s)
    if kubectl delete -n "$NAMESPACE" -f "$RENDERED_MANIFEST" --ignore-not-found; then
      cleanup_finished=$(date +%s)
      cleanup_duration=$((cleanup_finished - cleanup_started))
      printf '%s\n' "${GREEN}Benchmark resources deleted (${cleanup_duration}s)${RESET}"
    else
      printf '%s\n' "${YELLOW}warning:${RESET} failed to clean up Kubernetes resources for run $RUN_ID" >&2
    fi
  elif [ "$KEEP_RESOURCES" = true ] && [ "$APPLY_STARTED" = true ]; then
    printf '%s\n' "${YELLOW}Keeping benchmark resources (--keep-resources)${RESET}"
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
  "$MANIFEST_TEMPLATE" > "$RENDERED_MANIFEST"

cat > "$METADATA_LOG" <<EOF
run_id=$RUN_ID
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
kube_context=$KUBE_CONTEXT
client_node=$CLIENT_NODE
server_node=$SERVER_NODE
namespace=$NAMESPACE
image=$IMAGE
protocol=tcp
duration=$DURATION_DISPLAY
transfer_size=$TRANSFER_SIZE_DISPLAY
parallel_streams=$PARALLEL_STREAMS
service_name=$SERVER_SERVICE
server_pod=$SERVER_POD
client_pod=$CLIENT_POD
EOF

printf '\n%s\n' "${CYAN}Benchmark configuration${RESET}"
printf '  %-20s %s\n' "Run ID:" "$RUN_ID"
printf '  %-20s %s\n' "Kubernetes context:" "$KUBE_CONTEXT"
printf '  %-20s %s\n' "Client node:" "$CLIENT_NODE"
printf '  %-20s %s\n' "Server node:" "$SERVER_NODE"
printf '  %-20s %s\n' "Namespace:" "$NAMESPACE"
printf '  %-20s %s\n' "Image:" "$IMAGE"
printf '  %-20s %s\n' "Protocol:" "TCP"
printf '  %-20s %s\n' "Duration:" "$DURATION_DISPLAY"
printf '  %-20s %s\n' "Transfer size:" "$TRANSFER_SIZE_DISPLAY"
printf '  %-20s %s\n' "Parallel streams:" "$PARALLEL_STREAMS"
printf '\n'

for resource in "service/$SERVER_SERVICE" "pod/$SERVER_POD" "pod/$CLIENT_POD"; do
  if kubectl get -n "$NAMESPACE" "$resource" >/dev/null 2>&1; then
    fail "resource already exists: $resource; choose a different --run-id"
  fi
done

APPLY_STARTED=true
kubectl apply -n "$NAMESPACE" -f "$RENDERED_MANIFEST" >/dev/null
printf '%s\n' "${CYAN}Network service created${RESET}"
kubectl wait -n "$NAMESPACE" --for=jsonpath='{.status.phase}'=Running "pod/$SERVER_POD" --timeout="${TIMEOUT}s" >/dev/null
printf '%s\n' "${CYAN}Server pod running${RESET}"
kubectl wait -n "$NAMESPACE" --for=jsonpath='{.status.phase}'=Running "pod/$CLIENT_POD" --timeout="${TIMEOUT}s" >/dev/null
printf '%s\n' "${CYAN}Client pod running${RESET}"

# The manifest only starts long-running containers. Run the selected benchmark inside them.
# Keep the exec session attached so the server process is not killed when a remote
# background shell exits; server output is captured directly to the local log.
kubectl exec -n "$NAMESPACE" "$SERVER_POD" -- iperf3 -s --one-off > "$SERVER_LOG" 2>&1 &
SERVER_EXEC_PID=$!
sleep 2
if ! kill -0 "$SERVER_EXEC_PID" 2>/dev/null; then
  wait "$SERVER_EXEC_PID" 2>/dev/null || true
  SERVER_EXEC_PID=""
  fail "iperf3 server failed to start; see $SERVER_LOG"
fi
printf '%s\n' "${CYAN}Server benchmark running${RESET}"

CLIENT_COMMAND="iperf3 -c $SERVER_HOST -J -P $PARALLEL_STREAMS --get-server-output"
if [ -n "$TRANSFER_SIZE" ]; then
  CLIENT_COMMAND="$CLIENT_COMMAND -n $TRANSFER_SIZE"
else
  CLIENT_COMMAND="$CLIENT_COMMAND -t $DURATION"
fi

printf '%s\n' "${CYAN}Client benchmark running${RESET}"
kubectl exec -n "$NAMESPACE" "$CLIENT_POD" -- sh -c "$CLIENT_COMMAND" > "$CLIENT_LOG" 2>&1 &
CLIENT_EXEC_PID=$!

if [ -t 1 ]; then
  progress_started=$(date +%s)
  progress=0
  while kill -0 "$CLIENT_EXEC_PID" 2>/dev/null; do
    progress_now=$(date +%s)
    progress=$(((progress_now - progress_started) * 100 / DURATION))
    [ "$progress" -lt 99 ] || progress=99
    print_progress "$progress"
    sleep 1
  done
fi

set +e
wait "$CLIENT_EXEC_PID"
CLIENT_STATUS=$?
set -e
CLIENT_EXEC_PID=""
if [ -t 1 ]; then
  print_progress 100
  printf '\n'
fi

for _ in 1 2 3 4 5; do
  kill -0 "$SERVER_EXEC_PID" 2>/dev/null || break
  sleep 0.2
done
kill "$SERVER_EXEC_PID" 2>/dev/null || true
wait "$SERVER_EXEC_PID" 2>/dev/null || true
SERVER_EXEC_PID=""

printf 'finished_at=%s\nclient_status=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CLIENT_STATUS" >> "$METADATA_LOG"

CLIENT_LOG_DISPLAY="${CLIENT_LOG#$SCRIPT_DIR/}"
SERVER_LOG_DISPLAY="${SERVER_LOG#$SCRIPT_DIR/}"
METADATA_LOG_DISPLAY="${METADATA_LOG#$SCRIPT_DIR/}"
printf '\n%s\n' "${CYAN}Results${RESET}"
printf '%s\n' "  ${CYAN}Client result:${RESET} $GREEN$CLIENT_LOG_DISPLAY$RESET"
printf '%s\n' "  ${CYAN}Server log:${RESET}    $GREEN$SERVER_LOG_DISPLAY$RESET"
printf '%s\n' "  ${CYAN}Metadata:${RESET}      $GREEN$METADATA_LOG_DISPLAY$RESET"

if [ "$CLIENT_STATUS" -ne 0 ]; then
  printf '%s\n' "${RED}benchmark failed:${RESET} client output was saved to $CLIENT_LOG" >&2
  exit 1
fi

printf '%s\n\n' "${GREEN}Client benchmark completed successfully${RESET}"
