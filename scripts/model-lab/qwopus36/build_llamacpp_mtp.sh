#!/usr/bin/env bash
# Build llama.cpp from main with CUDA for Qwen3.6 MTP (PR #22673+).
set -euo pipefail

SRC="${LLAMA_SRC:-/mnt/data/src/llama.cpp}"
BUILD="${LLAMA_BUILD:-${SRC}/build}"
JOBS="${LLAMA_BUILD_JOBS:-$(nproc)}"

if [[ ! -d "${SRC}/.git" ]]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "${SRC}"
else
  git -C "${SRC}" fetch --depth 1 origin master 2>/dev/null || \
    git -C "${SRC}" fetch --depth 1 origin main 2>/dev/null || true
  git -C "${SRC}" checkout -f origin/master 2>/dev/null || \
    git -C "${SRC}" checkout -f origin/main 2>/dev/null || true
fi

export PATH="/usr/local/cuda/bin:${PATH}"
export CUDACXX="${CUDACXX:-/usr/local/cuda/bin/nvcc}"

cmake -S "${SRC}" -B "${BUILD}" \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_COMPILER="${CUDACXX}" \
  -DCMAKE_CUDA_ARCHITECTURES=86 \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF

cmake --build "${BUILD}" --config Release -j "${JOBS}" --target llama-server

echo "Built: ${BUILD}/bin/llama-server"
"${BUILD}/bin/llama-server" --version 2>&1 | head -3
