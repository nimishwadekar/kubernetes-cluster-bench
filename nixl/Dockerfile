# NIXLBench image for GPU KV-cache transfer benchmarks over UCX.
# Build arguments used by this Dockerfile:
#   CUDA_VERSION       CUDA devel image version used by FROM (default: 13.0.3)
#   UCX_VERSION        UCX Git tag or branch (default: v1.22.0)
#   NIXL_VERSION       NIXL Git tag or branch (default: v1.4.1)
#   ETCD_CPP_VERSION   etcd-cpp-apiv3 Git tag or branch (default: v0.15.4)
#   ABSL_VERSION       Abseil Git tag (default: 20250814.1)
#   GRPC_VERSION       gRPC Git tag (default: v1.73.0)
#   UCX_PREFIX         UCX installation path (default: /opt/ucx)
#   NIXL_PREFIX        NIXL installation path (default: /opt/nixl)
#   NIXLBENCH_PREFIX   NIXLBench installation path (default: /opt/nixlbench)
#
# Override any argument from the command line with --build-arg, for example:
#   docker buildx build --platform linux/amd64 \
#     --build-arg CUDA_VERSION=13.0.3 \
#     --build-arg UCX_VERSION=v1.22.0 \
#     --build-arg NIXL_VERSION=v1.4.1 \
#     --build-arg ETCD_CPP_VERSION=v0.15.4 \
#     --build-arg ABSL_VERSION=20250814.1 \
#     --build-arg GRPC_VERSION=v1.73.0 \
#     -f nixl/Dockerfile -t nixl-benchmark .

ARG CUDA_VERSION=13.0.3
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu24.04

ARG UCX_PREFIX=/opt/ucx
ARG NIXL_PREFIX=/opt/nixl
ARG NIXLBENCH_PREFIX=/opt/nixlbench

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        autoconf \
        automake \
        build-essential \
        ca-certificates \
        cmake \
        flex \
        git \
        hwloc \
        libaio-dev \
        libcpprest-dev \
        libcurl4-openssl-dev \
        libelf-dev \
        libclang-dev \
        libgflags-dev \
        libgtest-dev \
        libibumad-dev \
        libibverbs-dev \
        libhwloc-dev \
        liblz4-dev \
        libnuma-dev \
        libprotobuf-dev \
        librdmacm-dev \
        libssl-dev \
        libtool \
        libz-dev \
        libxml2-dev \
        libzstd-dev \
        ninja-build \
        pkg-config \
        protobuf-compiler \
        protobuf-compiler-grpc \
        python3 \
        python3-dev \
        python3-pip \
        python3-venv \
        rdma-core \
        uuid-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir \
        meson \
        ninja \
        pybind11 \
        tomlkit

ENV PATH=/opt/venv/bin:${UCX_PREFIX}/bin:${NIXLBENCH_PREFIX}/bin:${NIXL_PREFIX}/bin:${PATH}
ENV LD_LIBRARY_PATH=${NIXLBENCH_PREFIX}/lib:${NIXL_PREFIX}/lib:${NIXL_PREFIX}/lib/x86_64-linux-gnu:${NIXL_PREFIX}/lib/x86_64-linux-gnu/plugins:${UCX_PREFIX}/lib:/usr/local/lib:/usr/local/cuda/lib64

# Build UCX with CUDA and verbs support. GDRCopy is optional; UCX and NIXL
# remain functional without its kernel module.
ARG UCX_VERSION=v1.22.0
RUN git clone --depth 1 --branch "${UCX_VERSION}" \
        https://github.com/openucx/ucx.git /tmp/ucx && \
    cd /tmp/ucx && \
    ./autogen.sh && \
    ./contrib/configure-release-mt \
        --prefix="${UCX_PREFIX}" \
        --enable-shared \
        --disable-static \
        --disable-doxygen-doc \
        --enable-optimizations \
        --without-avx \
        --enable-cma \
        --enable-devel-headers \
        --with-cuda=/usr/local/cuda \
        --with-verbs \
        --with-dm && \
    make -j"$(nproc)" && \
    make install-strip && \
    rm -rf /tmp/ucx

# NIXL v1.4.1 requires Abseil logging support. Ubuntu's transitive Abseil
# packages are too old, so build Abseil and gRPC together from compatible
# pinned revisions before building the etcd client and NIXL.
ARG ABSL_VERSION=20250814.1
RUN git clone --depth 1 --branch "${ABSL_VERSION}" \
        https://github.com/abseil/abseil-cpp.git /tmp/abseil-cpp && \
    cmake -S /tmp/abseil-cpp -B /tmp/abseil-cpp-build \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_STANDARD=20 \
        -DBUILD_SHARED_LIBS=ON \
        -DABSL_PROPAGATE_CXX_STD=ON \
        -DABSL_ENABLE_INSTALL=ON \
        -DBUILD_TESTING=OFF && \
    cmake --build /tmp/abseil-cpp-build --parallel "$(nproc)" && \
    cmake --install /tmp/abseil-cpp-build && \
    rm -rf /tmp/abseil-cpp /tmp/abseil-cpp-build && \
    ldconfig

ARG GRPC_VERSION=v1.73.0
RUN git clone --depth 1 --branch "${GRPC_VERSION}" --recurse-submodules --shallow-submodules \
        https://github.com/grpc/grpc.git /tmp/grpc && \
    cmake -S /tmp/grpc -B /tmp/grpc-build \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_STANDARD=20 \
        -DBUILD_SHARED_LIBS=ON \
        -DgRPC_INSTALL=ON \
        -DgRPC_BUILD_TESTS=OFF \
        -DgRPC_ABSL_PROVIDER=package \
        -DgRPC_PROTOBUF_PROVIDER=module \
        -DgRPC_SSL_PROVIDER=package \
        -DgRPC_ZLIB_PROVIDER=package \
        -Dabsl_DIR=/usr/local/lib/cmake/absl \
        -DCMAKE_PREFIX_PATH=/usr/local && \
    cmake --build /tmp/grpc-build --parallel "$(nproc)" && \
    cmake --install /tmp/grpc-build && \
    rm -rf /tmp/grpc /tmp/grpc-build && \
    ldconfig

# Build the etcd C++ client used by NIXLBench for worker coordination.
ARG ETCD_CPP_VERSION=v0.15.4
RUN git clone --depth 1 --branch "${ETCD_CPP_VERSION}" \
        https://github.com/etcd-cpp-apiv3/etcd-cpp-apiv3.git /tmp/etcd-cpp-apiv3 && \
    sed -i '/^find_dependency(cpprestsdk)$/d' \
        /tmp/etcd-cpp-apiv3/etcd-cpp-api-config.in.cmake && \
    cmake -S /tmp/etcd-cpp-apiv3 -B /tmp/etcd-cpp-apiv3-build \
        -DBUILD_ETCD_CORE_ONLY=ON \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_STANDARD=20 \
        -DETCD_CMAKE_CXX_STANDARD=20 \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DCMAKE_PREFIX_PATH=/usr/local && \
    cmake --build /tmp/etcd-cpp-apiv3-build --parallel "$(nproc)" && \
    cmake --install /tmp/etcd-cpp-apiv3-build && \
    rm -rf /tmp/etcd-cpp-apiv3 /tmp/etcd-cpp-apiv3-build

# NIXL and NIXLBench are built from the same NIXL source revision so their
# headers, plugins, and benchmark ABI stay aligned.
ARG NIXL_VERSION=v1.4.1
RUN git clone --depth 1 --branch "${NIXL_VERSION}" \
        https://github.com/ai-dynamo/nixl.git /tmp/nixl && \
    meson setup /tmp/nixl-build /tmp/nixl \
        --prefix="${NIXL_PREFIX}" \
        --buildtype=release \
        -Ducx_path="${UCX_PREFIX}" \
        -Denable_plugins=UCX && \
    meson compile -C /tmp/nixl-build --parallel "$(nproc)" && \
    meson install -C /tmp/nixl-build && \
    meson setup /tmp/nixlbench-build /tmp/nixl/benchmark/nixlbench \
        --prefix="${NIXLBENCH_PREFIX}" \
        --buildtype=release \
        -Dnixl_path="${NIXL_PREFIX}" \
        -Dbuild_raw_cli=true \
        -Dbuild_tests=false && \
    meson compile -C /tmp/nixlbench-build --parallel "$(nproc)" && \
    meson install -C /tmp/nixlbench-build && \
    rm -rf /tmp/nixl /tmp/nixl-build /tmp/nixlbench-build

RUN ldconfig

CMD ["bash"]
