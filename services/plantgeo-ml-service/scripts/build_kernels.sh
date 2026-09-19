#!/usr/bin/env bash
# Compile every Mojo kernel into a Python extension module.
#
# Run through pixi (`pixi run build-kernels`), never with a bare `mojo`: on a Windows box with
# Strawberry Perl installed, `mojo` on the WSL PATH is Mojolicious. See
# `src/plantgeo_ml_service/method/kernels/AGENTS.md`.
set -euo pipefail

service_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_directory="${service_root}/kernels"
output_directory="${service_root}/src/plantgeo_ml_service/method/kernels/_native"

mkdir -p "${output_directory}"

for module_name in plantgeo_knn_search plantgeo_seasonal_bootstrap plantgeo_seasonal_features; do
  # The Mojo file is named after the kernel; the extension is named after the Python module, and
  # the two differ because `PyInit_<module>` has to match what the loader imports.
  source_stem="${module_name#plantgeo_}"
  echo "building ${module_name} from kernels/${source_stem}.mojo"
  # `--fp-mode contract=off` is NOT a tuning knob, it is the parity contract: Mojo defaults to
  # Clang's `-ffp-contract=fast` and fuses `offset + scale * value` into an FMA, which rounds once
  # where numpy rounds twice. Measured 2026-09-19 with contraction on: the neighbour distances
  # differed from the Python reference by up to 8.9e-16 and the bootstrap quantiles by 2.2e-16,
  # both of which fail the bit-identical assertion in tests/kernels/.
  mojo build \
    --emit shared-lib \
    --fp-mode contract=off \
    -I "${source_directory}" \
    -o "${output_directory}/${module_name}.so" \
    "${source_directory}/${source_stem}.mojo"
done

echo "kernels built into ${output_directory}"
