# ============================================================================
# Current active networking-only image
# ============================================================================
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

COPY system-packages.txt /tmp/system-packages.txt
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        apt-transport-https \
        $(cat /tmp/system-packages.txt) && \
    rm -rf /var/lib/apt/lists/* /tmp/system-packages.txt

CMD ["/bin/bash"]


# ============================================================================
# DISABLED GPU/RDMA BUILD — retained here for future re-enablement
# ============================================================================
# The following was the previous full CUDA/ROCm/Mellanox/libfabric/perftest
# image definition. Every line is commented out intentionally.
#
# # ============================================================================
# # Stage 1: Builder stage with full CUDA/ROCm toolkits and build dependencies
# # ============================================================================
# FROM ubuntu:24.04 AS builder
#
# ENV DEBIAN_FRONTEND=noninteractive
#
# RUN apt-get update && \
#     apt-get install -y --no-install-recommends \
#         wget \
#         gnupg2 \
#         ca-certificates \
#         apt-transport-https && \
#     rm -rf /var/lib/apt/lists/*
#
# # CUDA toolkit
# RUN wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb && \
#     dpkg -i cuda-keyring_1.1-1_all.deb && \
#     rm cuda-keyring_1.1-1_all.deb && \
#     apt-get update && \
#     apt-get install -y --no-install-recommends cuda-toolkit-12-8 && \
#     rm -rf /var/lib/apt/lists/*
#
# ENV PATH=/usr/local/cuda-12.8/bin:${PATH}
# ENV LD_LIBRARY_PATH=/usr/local/cuda-12.8/lib64:${LD_LIBRARY_PATH}
#
# # ROCm toolkit
# RUN wget https://repo.radeon.com/rocm/rocm.gpg.key -O - | gpg --dearmor -o /usr/share/keyrings/rocm-archive-keyring.gpg && \
#     echo "deb [arch=amd64 signed-by=/usr/share/keyrings/rocm-archive-keyring.gpg] https://repo.radeon.com/rocm/apt/6.3 noble main" > /etc/apt/sources.list.d/rocm.list && \
#     echo 'Package: *\nPin: release o=repo.radeon.com\nPin-Priority: 600' > /etc/apt/preferences.d/rocm-pin-600 && \
#     apt-get update && \
#     apt-get install -y --no-install-recommends rocm-dev && \
#     rm -rf /var/lib/apt/lists/*
#
# ENV PATH=/opt/rocm/bin:${PATH}
# ENV LD_LIBRARY_PATH=/opt/rocm/lib:${LD_LIBRARY_PATH}
#
# # Mellanox OFED/RDMA build dependencies
# RUN echo "deb [trusted=yes] https://linux.mellanox.com/public/repo/mlnx_ofed/latest/ubuntu24.04/x86_64 ./" > /etc/apt/sources.list.d/mlnx_ofed.list && \
#     apt-get update && \
#     apt-get install -y --no-install-recommends \
#         libibverbs-dev librdmacm-dev libibumad-dev libpci-dev \
#         ibverbs-utils perftest && \
#     rm -rf /var/lib/apt/lists/*
#
# RUN apt-get update && \
#     apt-get install -y --no-install-recommends \
#         git build-essential autoconf automake libtool pkg-config && \
#     rm -rf /var/lib/apt/lists/*
#
# # libfabric and fabtests
# RUN git clone --branch v2.6.0 --depth 1 https://github.com/ofiwg/libfabric.git /tmp/libfabric && \
#     cd /tmp/libfabric && ./autogen.sh && \
#     ./configure --prefix=/usr/local/libfabric \
#         --enable-efa --enable-verbs --enable-shm --enable-mrail \
#         --with-cuda=/usr/local/cuda --enable-cuda-dlopen && \
#     make -j$(nproc) && make install
#
# RUN cd /tmp/libfabric/fabtests && ./autogen.sh && \
#     ./configure --prefix=/usr/local/libfabric \
#         --with-libfabric=/usr/local/libfabric \
#         --with-cuda=/usr/local/cuda && \
#     make -j$(nproc) && make install
#
# # GPU-enabled perftest
# RUN git clone https://github.com/linux-rdma/perftest.git /tmp/perftest && \
#     cd /tmp/perftest && git checkout tags/25.10.0-0.128 && \
#     ./autogen.sh && \
#     export CUDA_H_PATH=/usr/local/cuda/include/cuda.h && \
#     ./configure --prefix=/usr/local/perftest-gpu \
#         --enable-cuda --with-cuda=/usr/local/cuda \
#         --enable-rocm --with-rocm=/opt/rocm && \
#     make -j$(nproc) && make install
#
# # ============================================================================
# # Stage 2: Final runtime image
# # ============================================================================
# FROM ubuntu:24.04
# ENV DEBIAN_FRONTEND=noninteractive
#
# RUN apt-get update && \
#     apt-get install -y --no-install-recommends \
#         wget gnupg2 ca-certificates apt-transport-https && \
#     rm -rf /var/lib/apt/lists/*
#
# # CUDA runtime
# RUN wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb && \
#     dpkg -i cuda-keyring_1.1-1_all.deb && rm cuda-keyring_1.1-1_all.deb && \
#     apt-get update && \
#     apt-get install -y --no-install-recommends cuda-cudart-12-8 cuda-nvrtc-12-8 && \
#     rm -rf /var/lib/apt/lists/*
#
# # ROCm runtime and AMD SMI
# RUN wget https://repo.radeon.com/rocm/rocm.gpg.key -O - | gpg --dearmor -o /usr/share/keyrings/rocm-archive-keyring.gpg && \
#     echo "deb [arch=amd64 signed-by=/usr/share/keyrings/rocm-archive-keyring.gpg] https://repo.radeon.com/rocm/apt/6.3 noble main" > /etc/apt/sources.list.d/rocm.list && \
#     apt-get update && \
#     apt-get install -y --no-install-recommends \
#         hip-runtime-amd rocm-core amd-smi-lib python3 python3-pip python3-yaml && \
#     pip3 install --break-system-packages amdsmi && \
#     rm -rf /var/lib/apt/lists/*
#
# ENV PATH=/usr/local/cuda-12.8/bin:/opt/rocm/bin:/usr/sbin:${PATH}
# ENV LD_LIBRARY_PATH=/usr/local/cuda-12.8/lib64:/opt/rocm/lib:${LD_LIBRARY_PATH}
#
# # Mellanox OFED/RDMA runtime
# RUN echo "deb [trusted=yes] https://linux.mellanox.com/public/repo/mlnx_ofed/latest/ubuntu24.04/x86_64 ./" > /etc/apt/sources.list.d/mlnx_ofed.list && \
#     apt-get update && \
#     apt-get install -y --no-install-recommends \
#         libibverbs1 librdmacm1 libibumad3 libibmad5 libpci3 \
#         ibverbs-utils ibverbs-providers rdma-core mlnx-tools \
#         mlnx-ofed-kernel-utils mft mstflint infiniband-diags && \
#     rm -rf /var/lib/apt/lists/*
#
# COPY --from=builder /usr/local/perftest-gpu/bin/* /usr/local/bin/
# COPY --from=builder /usr/local/libfabric /usr/local/libfabric
# ENV PATH=/usr/local/libfabric/bin:${PATH}
# ENV LD_LIBRARY_PATH=/usr/local/libfabric/lib:${LD_LIBRARY_PATH}
# RUN update-pciids
# RUN sed -i 's/#force_color_prompt=yes/force_color_prompt=yes/' /root/.bashrc
#
# CMD ["/bin/bash"]
