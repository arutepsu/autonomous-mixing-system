#!/bin/sh
# Runs inside the gcc container with the build tree mounted at /w. Builds two harnesses from the unmodified dist10
# sources, each with one observation patch of l3psy.c, using the makefile.unix flags (-g -DUNIX, no optimisation):
#   A  harness         + l3psy_probe.c          encoder-path capture  -> /w/out/encoder_path/  (whole granules only)
#   B  harness_mapping + l3psy_mapping_probe.c  long-band mapping probe -> /w/out/long_mapping/ (every input)
# Both run from the encoder directory so that "tables/psy_data" and "tables/absthr_*" resolve.
set -eu
cd /w/src
gcc --version | head -1 > /w/toolchain.txt
uname -m >> /w/toolchain.txt
COMMON="common.c subs.c psy.c tonal.c portableio.c ieeefloat.c -lm"
gcc -g -DUNIX -o harness harness.c l3psy_probe.c $COMMON 2> /w/build.log || { cat /w/build.log; exit 1; }
gcc -g -DUNIX -o harness_mapping harness_mapping.c l3psy_mapping_probe.c $COMMON 2> /w/build_mapping.log || {
  cat /w/build_mapping.log; exit 1; }
mkdir -p /w/out/encoder_path /w/out/long_mapping
for f in /w/inputs/*.raw; do
  name=$(basename "$f" .raw)
  if [ $(( $(stat -c %s "$f") % 1152 )) -eq 0 ]; then
    ./harness 44100 "$f" > "/w/out/encoder_path/$name.jsonl" 2> "/w/out/encoder_path/$name.stderr"
  fi
  ./harness_mapping 44100 "$f" > "/w/out/long_mapping/$name.jsonl" 2> "/w/out/long_mapping/$name.stderr"
done
sha256sum harness harness_mapping l3psy_probe.c l3psy_mapping_probe.c > /w/build.sha256
