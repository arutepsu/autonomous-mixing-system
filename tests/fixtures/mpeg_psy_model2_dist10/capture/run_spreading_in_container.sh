#!/bin/sh
# Probe F capture (spreading probe; the probe C, D and E call lists are unchanged). Runs inside the gcc container
# with the build tree mounted at /w; probes A-E stay with their own scripts. Builds harness_spreading from the
# unmodified dist10 sources plus l3psy_spreading_probe.c (probes B + C + D + E + F) and subs_short_probe.c, with the
# makefile.unix flags (-g -DUNIX, no optimisation), and runs it on every input from the encoder directory so that the
# tables resolve.
set -eu
cd /w/src
gcc --version | head -1 > /w/toolchain_spreading.txt
uname -m >> /w/toolchain_spreading.txt
gcc -g -DUNIX -o harness_spreading harness_spreading.c l3psy_spreading_probe.c subs_short_probe.c common.c psy.c tonal.c \
  portableio.c ieeefloat.c -lm 2> /w/build_spreading.log || { cat /w/build_spreading.log; exit 1; }
mkdir -p /w/out/spreading
# Probe C calls: identical to run_fft_in_container.sh, so lines without probe D fields match probe C's output.
c_calls() {
  case "$1" in
    silence_12granules) echo 2 ;;
    impulse_32767_at1983|impulse_32767_at1984) echo 3,4 ;;
    masker_1k_a8192|masker_1k_a81.92|target_2k_a1024|target_2k_a10.24) echo 6 ;;
    *) echo - ;;
  esac
}
# Probe D calls: identical to run_short_in_container.sh.
d_calls() {
  case "$1" in
    silence_12granules) echo 0,1,2 ;;
    impulse_32767_at1983) echo 2,3,4,5 ;;
    burst_1k_a8192_onset2000) echo 3,4,5 ;;
    masker_1k_a8192) echo 0,1,2,3,4 ;;
    target_2k_a10.24) echo 6 ;;
    *) echo - ;;
  esac
}
# Probe E calls: identical to run_partition_in_container.sh.
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
# Probe F calls: every probe E call (silence and start-up, impulse and burst onsets including burst call 4, the
# established tone and the quiet tone).
f_calls() { e_calls "$1"; }
for f in /w/inputs/*.raw; do
  name=$(basename "$f" .raw)
  ./harness_spreading 44100 "$f" "$(c_calls "$name")" "$(d_calls "$name")" "/w/out/spreading/$name.window_s.json" \
    "$(e_calls "$name")" "/w/out/spreading/$name.mapping.json" "$(f_calls "$name")" "/w/out/spreading/$name.s3.json" \
    > "/w/out/spreading/$name.jsonl" 2> "/w/out/spreading/$name.stderr"
done
sha256sum harness_spreading l3psy_spreading_probe.c subs_short_probe.c > /w/build_spreading.sha256
