#!/usr/bin/env bash
set -euo pipefail
dir="${STRATA_ASSETS:-${FREIBURG_ROOT:-/srv/freiburg-openrag}/assets}/models/unsloth-UD-Q4_K_XL"
mkdir -p "$dir"
cd "$dir"
rev=38bb39ee97821de2c9009abb7e93950eec396e66
fetch() {
  n=$1
  digest=$2
  name="Qwen3.8-Flash-Next-UD-Q4_K_XL-0000$n-of-00004.gguf"
  if test -f "$name" && printf '%s  %s\n' "$digest" "$name" | sha256sum -c -; then return; fi
  curl -fsSL --connect-timeout 15 --speed-limit 2048 --speed-time 120 --retry 8 --retry-delay 2 --retry-all-errors -C - -o "$name.part" "https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF/resolve/$rev/UD-Q4_K_XL/$name"
  printf '%s  %s\n' "$digest" "$name.part" | sha256sum -c -
  mv "$name.part" "$name"
}
fetch 1 4448186216b3af4cc558bbce2c3213f01608f8f8b2e5267a9767971dd3ec8082 & a=$!
fetch 2 3f342f1c1580473f1ee94ddd5b28206e8c07a70fa1a366f59d1d6c922919a6c9 & b=$!
fetch 3 56758f40269cad5cd9b0d3d6fbae0f40f6d5be6de49e4ab392dbe83157d9cbd3 & c=$!
fetch 4 753bda48b98ba4f1636134a90a967de1b2d3908a236c026e464777342e53510a & d=$!
failed=0
for job in "$a" "$b" "$c" "$d"; do wait "$job" || failed=1; done
exit "$failed"
