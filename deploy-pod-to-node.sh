#!/bin/bash

# Parse arguments
nvidia_gpu_count=""
amd_gpu_count=""
node_name=""
namespace=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --request-nvidia-gpus)
            nvidia_gpu_count="$2"
            shift 2
            ;;
        --request-amd-gpus)
            amd_gpu_count="$2"
            shift 2
            ;;
        -n|--namespace)
            namespace="$2"
            shift 2
            ;;
        *)
            if [ -z "$node_name" ]; then
                node_name="$1"
            else
                echo "Error: Unknown argument '$1'"
                echo "Usage: $0 <node_name> [--namespace <namespace>] [--request-nvidia-gpus <N>] [--request-amd-gpus <N>]"
                exit 1
            fi
            shift
            ;;
    esac
done

# If namespace not specified, get it from current kubectl context
if [ -z "$namespace" ]; then
    namespace=$(kubectl config view --minify --output 'jsonpath={..namespace}' 2>/dev/null)
    # If no namespace is set in the current context, fall back to "default"
    if [ -z "$namespace" ]; then
        namespace="default"
    fi
fi

if [ -z "$node_name" ]; then
    echo "Usage: $0 <node_name> [--namespace <namespace>] [--request-nvidia-gpus <N>] [--request-amd-gpus <N>]"
    echo ""
    echo "Arguments:"
    echo "  <node_name>                  Name of the Kubernetes node to deploy the pod on"
    echo "  -n, --namespace <ns>         Optional: Kubernetes namespace (default: current context namespace, or 'default')"
    echo "  --request-nvidia-gpus <N>    Optional: Number of NVIDIA GPUs to request (omitted if not specified)"
    echo "  --request-amd-gpus <N>       Optional: Number of AMD GPUs to request (omitted if not specified)"
    exit 1
fi

#pod_name="networking-debug-pod-$node_name"
pod_name="test-pod-$node_name"

# Check if pod already exists
if kubectl get pod "$pod_name" -n "$namespace" &>/dev/null; then
    echo "⚠️  Pod '$pod_name' already exists in namespace '$namespace'"
    echo ""
    read -p "Do you want to delete the existing pod and deploy a new one? (y/n): " -n 1 -r
    echo ""
    
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Deployment aborted by user."
        exit 0
    fi
    
    echo "Deleting existing pod..."
    kubectl delete pod "$pod_name" -n "$namespace"
    echo "Waiting for pod to be fully deleted..."
    kubectl wait --for=delete pod/"$pod_name" -n "$namespace" --timeout=60s 2>/dev/null || true
fi

# Apply manifest with dynamic podName, nodeName, namespace, and GPU count substitution
echo "Deploying pod '$pod_name' to node: $node_name in namespace: $namespace"

# Build GPU info message
gpu_info=""
if [ -n "$nvidia_gpu_count" ]; then
    gpu_info="$nvidia_gpu_count NVIDIA GPU(s)"
fi
if [ -n "$amd_gpu_count" ]; then
    [ -n "$gpu_info" ] && gpu_info="$gpu_info, "
    gpu_info="${gpu_info}$amd_gpu_count AMD GPU(s)"
fi
if [ -n "$gpu_info" ]; then
    echo "  Requesting: $gpu_info"
fi

# Build sed command - base substitutions
sed_cmd="s/REPLACE_POD_NAME/$pod_name/;s/REPLACE_NODE_NAME/$node_name/;s/REPLACE_NAMESPACE/$namespace/"

# Handle NVIDIA GPU: substitute if provided, delete line if not
if [ -n "$nvidia_gpu_count" ]; then
    sed_cmd="$sed_cmd;s/REPLACE_NVIDIA_GPU_COUNT/$nvidia_gpu_count/"
else
    sed_cmd="$sed_cmd;/nvidia.com\/gpu:/d"
fi

# Handle AMD GPU: substitute if provided, delete line if not
if [ -n "$amd_gpu_count" ]; then
    sed_cmd="$sed_cmd;s/REPLACE_AMD_GPU_COUNT/$amd_gpu_count/"
else
    sed_cmd="$sed_cmd;/amd.com\/gpu:/d"
fi


if ! sed -e "$sed_cmd" networking-debug-pod.yaml | kubectl apply -f -; then
    echo "✗ Pod deployment failed."
    exit 1
fi

echo "✓ Pod deployment initiated. Check status with: kubectl get pod $pod_name -n $namespace"

