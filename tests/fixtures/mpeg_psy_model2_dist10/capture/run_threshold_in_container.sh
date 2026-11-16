#!/bin/sh
# Probe G capture (threshold probe; the probe C, D, E and F call lists are unchanged). Runs inside the gcc container
# with the build tree mounted at /w; probes A-F stay with their own scripts. Builds harness_threshold from the
# unmodified dist10 sources plus l3psy_threshold_probe.c (probes B + C + D + E + F + G) and subs_short_probe.c, with
# the makefile.unix flags (-g -DUNIX, no optimisation), and runs it on every input from the encoder directory so that
# the tables resolve.
set -eu
cd /w/src
gcc --version | head -1 > /w/toolchain_threshold.txt
uname -m >> /w/toolchain_threshold.txt
gcc -g -DUNIX -o harness_threshold harness_threshold.c l3psy_threshold_probe.c subs_short_probe.c common.c psy.c tonal.c \
  portableio.c ieeefloat.c -lm 2> /w/build_threshold.log || { cat /w/build_threshold.log; exit 1; }
mkdir -p /w/out/threshold
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
# Probe G calls: consecutive calls from start-up (zero history) through established history for the 1 kHz tone
# (calls 0-5; call 3 is the named masker case) and every call of the burst (calls 0-3 silent, onset at call 4, the
# named burst case, then 5-7).
g_calls() {
  case "$1" in
    masker_1k_a8192) echo 0,1,2,3,4,5 ;;
    burst_1k_a8192_onset2000) echo 0,1,2,3,4,5,6,7 ;;
    *) echo - ;;
  esac
}
for f in /w/inputs/*.raw; do
  name=$(basename "$f" .raw)
  ./harness_threshold 44100 "$f" "$(c_calls "$name")" "$(d_calls "$name")" "/w/out/threshold/$name.window_s.json" \
    "$(e_calls "$name")" "/w/out/threshold/$name.mapping.json" "$(f_calls "$name")" "/w/out/threshold/$name.s3.json" \
    "$(g_calls "$name")" "/w/out/threshold/$name.tables.json" > "/w/out/threshold/$name.jsonl" 2> "/w/out/threshold/$name.stderr"
done
sha256sum harness_threshold l3psy_threshold_probe.c subs_short_probe.c > /w/build_threshold.sha256
