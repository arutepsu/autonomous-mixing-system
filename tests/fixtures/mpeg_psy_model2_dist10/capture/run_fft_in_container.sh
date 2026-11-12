#!/bin/sh
# Probe C capture (long-FFT probe). Runs inside the gcc container with the build tree mounted at /w; probes A and B
# stay with run_in_container.sh. Builds harness_fft from the unmodified dist10 sources plus l3psy_fft_probe.c
# (probe B + C) and subs_fft_probe.c, with the makefile.unix flags (-g -DUNIX, no optimisation), and runs it on
# every input from the encoder directory so that "tables/psy_data" and "tables/absthr_*" resolve.
set -eu
cd /w/src
gcc --version | head -1 > /w/toolchain_fft.txt
uname -m >> /w/toolchain_fft.txt
gcc -g -DUNIX -o harness_fft harness_fft.c l3psy_fft_probe.c subs_fft_probe.c common.c psy.c tonal.c portableio.c \
  ieeefloat.c -lm 2> /w/build_fft.log || { cat /w/build_fft.log; exit 1; }
mkdir -p /w/out/long_fft
# Selected calls (FFT-stage arrays); every other call prints probe B fields only.
calls_for() {
  case "$1" in
    silence_12granules) echo 2 ;;
    impulse_32767_at1983|impulse_32767_at1984) echo 3,4 ;;
    masker_1k_a8192|masker_1k_a81.92|target_2k_a1024|target_2k_a10.24) echo 6 ;;
    *) echo - ;;
  esac
}
for f in /w/inputs/*.raw; do
  name=$(basename "$f" .raw)
  ./harness_fft 44100 "$f" "$(calls_for "$name")" "/w/out/long_fft/$name.window.json" \
    > "/w/out/long_fft/$name.jsonl" 2> "/w/out/long_fft/$name.stderr"
done
sha256sum harness_fft l3psy_fft_probe.c subs_fft_probe.c > /w/build_fft.sha256
