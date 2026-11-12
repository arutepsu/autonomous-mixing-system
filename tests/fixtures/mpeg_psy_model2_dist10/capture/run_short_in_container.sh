#!/bin/sh
# Probe D capture (short-FFT / unpredictability probe). Runs inside the gcc container with the build tree mounted at
# /w; probes A-C stay with their own scripts. Builds harness_short from the unmodified dist10 sources plus
# l3psy_short_probe.c (probes B + C + D) and subs_short_probe.c, with the makefile.unix flags (-g -DUNIX, no
# optimisation), and runs it on every input from the encoder directory so that the tables resolve.
set -eu
cd /w/src
gcc --version | head -1 > /w/toolchain_short.txt
uname -m >> /w/toolchain_short.txt
gcc -g -DUNIX -o harness_short harness_short.c l3psy_short_probe.c subs_short_probe.c common.c psy.c tonal.c \
  portableio.c ieeefloat.c -lm 2> /w/build_short.log || { cat /w/build_short.log; exit 1; }
mkdir -p /w/out/short
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
for f in /w/inputs/*.raw; do
  name=$(basename "$f" .raw)
  ./harness_short 44100 "$f" "$(c_calls "$name")" "$(d_calls "$name")" "/w/out/short/$name.window_s.json" \
    > "/w/out/short/$name.jsonl" 2> "/w/out/short/$name.stderr"
done
sha256sum harness_short l3psy_short_probe.c subs_short_probe.c > /w/build_short.sha256
