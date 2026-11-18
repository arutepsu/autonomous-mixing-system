#!/bin/sh
# Probe E capture (partition probe; probe D's call lists unchanged). Runs inside the gcc container with the build tree mounted at
# /w; probes A-D stay with their own scripts. Builds harness_partition from the unmodified dist10 sources plus
# l3psy_partition_probe.c (probes B + C + D + E) and subs_short_probe.c, with the makefile.unix flags (-g -DUNIX, no
# optimisation), and runs it on every input from the encoder directory so that the tables resolve.
set -eu
cd /w/src
gcc --version | head -1 > /w/toolchain_partition.txt
uname -m >> /w/toolchain_partition.txt
gcc -g -DUNIX -o harness_partition harness_partition.c l3psy_partition_probe.c subs_short_probe.c common.c psy.c tonal.c \
  portableio.c ieeefloat.c -lm 2> /w/build_partition.log || { cat /w/build_partition.log; exit 1; }
mkdir -p /w/out/partition
# Probe C calls: identical to run_fft_in_container.sh, so lines without probe D fields match probe C's output.
c_calls() {
  case "$1" in
    silence_12granules) echo 2 ;;
    impulse_32767_at1983|impulse_32767_at1984) echo 3,4 ;;
    masker_1k_a8192|masker_1k_a81.92|target_2k_a1024|target_2k_a10.24) echo 6 ;;
    *) echo - ;;
  esac
}
# Probe D calls: consecutive runs covering zero-initialised history (calls 0-1) and an established predictor.
d_calls() {
  case "$1" in
    silence_12granules) echo 0,1,2 ;;
    impulse_32767_at1983) echo 2,3,4,5 ;;
    burst_1k_a8192_onset2000) echo 3,4,5 ;;
    masker_1k_a8192) echo 0,1,2,3,4 ;;
    # partially floored active short block: call 6, block 2 floors exactly short bins 42 and 86 of 0-127
    target_2k_a10.24) echo 6 ;;
    *) echo - ;;
  esac
}
# Probe E calls: a compact subset of the probe D calls (silence and start-up, impulse onset, burst onset, the quiet
# tone with the partially floored short block, and an established tone).
e_calls() {
  case "$1" in
    silence_12granules) echo 0,1,2 ;;
    impulse_32767_at1983) echo 3,4 ;;
    burst_1k_a8192_onset2000) echo 4 ;;
    masker_1k_a8192) echo 3 ;;
    target_2k_a10.24) echo 6 ;;
    *) echo - ;;
  esac
}
for f in /w/inputs/*.raw; do
  name=$(basename "$f" .raw)
  ./harness_partition 44100 "$f" "$(c_calls "$name")" "$(d_calls "$name")" "/w/out/partition/$name.window_s.json" \
    "$(e_calls "$name")" "/w/out/partition/$name.mapping.json" \
    > "/w/out/partition/$name.jsonl" 2> "/w/out/partition/$name.stderr"
done
sha256sum harness_partition l3psy_partition_probe.c subs_short_probe.c > /w/build_partition.sha256
