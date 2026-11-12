"""Create probe C ("long-FFT probe") sources: observation-only copies of the long-window FFT stage of dist10.

Usage: python make_probe_fft.py <l3psy_mapping_probe.c> <original subs.c> <out l3psy_fft_probe.c> <out subs_fft_probe.c>

Probe C is built on probe B (``make_probe_mapping.py`` output), so the same binary also reports probe B's
long-band values and the capture can be compared with ``expected_long_mapping`` call by call. It adds:

l3psy.c (Layer III path, around the long FFT call at original l. 483):
  - before ``fft(wsamp_r, ...)``: copies of ``window[0..1023]`` (the Hann coefficients) and ``wsamp_r[0..1023]``
    (the windowed samples passed to the FFT; the FFT overwrites this array in place), then arms the FFT probe;
  - after it: disarms and copies ``energy[0..1023]`` and ``phi[0..1023]`` as returned (natural bin order).
subs.c (``fft``):
  - in the final butterfly stage, while armed, a copy of each ``energy[i]`` / ``energy[ip]`` immediately after it
    is computed and before the 0.0005 floor test (pre-reorder position);
  - in the bit-reversal reorder, while armed, the same swap applied to that copy, so it ends in natural bin order.

Only probe globals and block-local loop counters are written; no encoder variable is assigned. The short FFTs and
Layers I/II never arm the probe. The two unified diffs are printed (l3psy relative to probe B, subs relative to
the original) so the modification can be recorded with the fixture.
"""

import difflib
import sys

mapping_src, subs_src, l3_out, subs_out = sys.argv[1:5]


def read(path: str) -> tuple[str, str, list[str]]:
    text = open(path, encoding="ascii", newline="").read()
    nl = "\r\n" if "\r\n" in text else "\n"
    return text, nl, text.split(nl)


def only(lines: list[str], predicate, what: str) -> int:
    hits = [n for n, line in enumerate(lines) if predicate(line)]
    assert len(hits) == 1, (what, hits)
    return hits[0]


# --- l3psy (on top of probe B) ---
l3_text, l3_nl, l3 = read(mapping_src)
i = only(l3, lambda s: s.startswith("static double probe_b_temp1[CBANDS];"), "probe B globals")
l3[i + 1 : i + 1] = [
    "/* --- long-FFT probe C (fixture capture only; copies values, writes no encoder variable) --- */",
    "FLOAT probe_c_window[BLKSIZE], probe_c_wsamp[BLKSIZE], probe_c_energy[BLKSIZE], probe_c_phi[BLKSIZE];",
    "int probe_c_fft_armed;",
]
k = only(l3, lambda s: "fft( wsamp_r, wsamp_i, energy, phi, 1024 );" in s and "long FFT" in s, "long FFT call")
l3[k:k] = [
    "\t{ int probe_c_n; for ( probe_c_n = 0; probe_c_n < BLKSIZE; probe_c_n++ ) { /* probe C */",
    "\t    probe_c_window[probe_c_n] = window[probe_c_n]; probe_c_wsamp[probe_c_n] = wsamp_r[probe_c_n]; } }",
    "\tprobe_c_fft_armed = 1; /* probe C */",
]
k += 3
l3[k + 1 : k + 1] = [
    "\tprobe_c_fft_armed = 0; /* probe C */",
    "\t{ int probe_c_n; for ( probe_c_n = 0; probe_c_n < BLKSIZE; probe_c_n++ ) { /* probe C */",
    "\t    probe_c_energy[probe_c_n] = energy[probe_c_n]; probe_c_phi[probe_c_n] = phi[probe_c_n]; } }",
]
l3_new = l3_nl.join(l3)
open(l3_out, "w", encoding="ascii", newline="").write(l3_new)

# --- subs.c fft ---
s_text, s_nl, subs = read(subs_src)
i = only(subs, lambda s: s.startswith('#include "encoder.h"'), "subs includes")
subs[i + 1 : i + 1] = [
    "/* --- long-FFT probe C (fixture capture only; copies values, writes no encoder variable) --- */",
    "extern int probe_c_fft_armed;",
    "FLOAT probe_c_energy_prefloor[BLKSIZE];",
]
e = only(subs, lambda s: s.strip() == "energy[i] = x_real[i]*x_real[i] + x_imag[i]*x_imag[i];", "even energy")
subs.insert(e + 1, "    if (probe_c_fft_armed) probe_c_energy_prefloor[i] = energy[i]; /* probe C, before floor */")
o = only(subs, lambda s: s.strip() == "energy[ip] = x_real[ip]*x_real[ip] + x_imag[ip]*x_imag[ip];", "odd energy")
subs.insert(o + 1, "    if (probe_c_fft_armed) probe_c_energy_prefloor[ip] = energy[ip]; /* probe C */")
r = only(subs, lambda s: s.strip() == "phi[i] = t_real;", "reorder phi swap")
subs.insert(
    r + 1,
    "       if (probe_c_fft_armed) { FLOAT probe_c_t = probe_c_energy_prefloor[j]; /* probe C: same reorder */"
    " probe_c_energy_prefloor[j] = probe_c_energy_prefloor[i]; probe_c_energy_prefloor[i] = probe_c_t; }",
)
s_new = s_nl.join(subs)
open(subs_out, "w", encoding="ascii", newline="").write(s_new)

for old, new, a, b, nl in (
    (l3_text, l3_new, "l3psy_mapping_probe.c", "l3psy_fft_probe.c", l3_nl),
    (s_text, s_new, "subs.c", "subs_fft_probe.c", s_nl),
):
    diff = difflib.unified_diff(old.split(nl), new.split(nl), a, b, lineterm="", n=1)
    sys.stdout.write("\n".join(diff) + "\n")
