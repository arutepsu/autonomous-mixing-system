"""Create probe D ("short-FFT / unpredictability probe") sources: observation-only copies, built on probe C.

Usage: python make_probe_short.py <l3psy_fft_probe.c> <subs_fft_probe.c> <out l3psy_short_probe.c> <out subs_short_probe.c>

Probe D is built on probe C (``make_probe_fft.py`` output, itself built on probe B), so one binary also reports
probe B's long-band fields and probe C's long-FFT arrays, and the capture can be compared with both. It adds, in
the Layer III path of ``L3psycho_anal``:
  - before the long-FFT predictor loop (bins 0-5): the age indices new/old/oldest and copies of
    r[chn][old][0..5], r[chn][oldest][0..5], phi_sav[chn][old][0..5], phi_sav[chn][oldest][0..5]
    (``oldest`` aliases the ``new`` slot, so its value must be read before the loop overwrites it);
  - after that loop: r[chn][new][0..5] and phi_sav[chn][new][0..5] as stored for the next calls;
  - in the short-FFT loop, before each ``fft(..., 256)``: a copy of wsamp_r[0..255] (the windowed short block)
    and the arming of the short-FFT pre-floor copy for that block; disarmed after the call;
  - after ``cw[j] = 0.4`` for lines 206..512: copies of cw[0..512], energy_s and phi_s, and window_s.
In ``fft`` (subs.c), while armed by a short-block call only: each energy right after it is computed and before the
floor test, with the same bit-reversal swap (as probe C does for the long FFT).

Only probe globals and block-local counters are written; no encoder variable is assigned. The two unified diffs
are printed (relative to the probe C sources) so the modification can be recorded with the fixture.
"""

import difflib
import sys

l3_src, subs_src, l3_out, subs_out = sys.argv[1:5]


def read(path: str) -> tuple[str, str, list[str]]:
    text = open(path, encoding="ascii", newline="").read()
    nl = "\r\n" if "\r\n" in text else "\n"
    return text, nl, text.split(nl)


def find(lines: list[str], predicate, what: str, first: bool = False) -> int:
    hits = [n for n, line in enumerate(lines) if predicate(line)]
    assert hits and (first or len(hits) == 1), (what, hits)
    return hits[0]


N6 = "for ( probe_d_n = 0; probe_d_n < 6; probe_d_n++ )"

# --- l3psy (on top of probe C) ---
l3_text, l3_nl, l3 = read(l3_src)
i = find(l3, lambda s: s.startswith("int probe_c_fft_armed;"), "probe C globals")
l3[i + 1 : i + 1] = [
    "/* --- short-FFT / unpredictability probe D (fixture capture only; copies values, writes no encoder variable) --- */",
    "FLOAT probe_d_window_s[BLKSIZE_s], probe_d_wsamp_s[3][BLKSIZE_s], probe_d_prefloor_s[3][BLKSIZE_s];",
    "FLOAT probe_d_energy_s[3][BLKSIZE_s], probe_d_phi_s[3][BLKSIZE_s];",
    "FLOAT probe_d_r_old[6], probe_d_r_oldest[6], probe_d_phi_old[6], probe_d_phi_oldest[6];",
    "FLOAT probe_d_r_new[6], probe_d_phi_new[6];",
    "double probe_d_cw[HBLKSIZE];",
    "int probe_d_age_new, probe_d_age_old, probe_d_age_oldest;",
    "FLOAT *probe_d_short_target; int probe_d_short_armed;",
]
k = find(l3, lambda s: s.strip() == "for ( j = 0; j < 6; j++ )", "long predictor loop")
l3[k:k] = [
    f"\t{{ int probe_d_n; {N6} {{ /* probe D: history before the loop */",
    "\t    probe_d_r_old[probe_d_n] = r[chn][old][probe_d_n]; probe_d_r_oldest[probe_d_n] = r[chn][oldest][probe_d_n];",
    "\t    probe_d_phi_old[probe_d_n] = phi_sav[chn][old][probe_d_n];",
    "\t    probe_d_phi_oldest[probe_d_n] = phi_sav[chn][oldest][probe_d_n]; }",
    "\t  probe_d_age_new = new; probe_d_age_old = old; probe_d_age_oldest = oldest; }",
]
s = find(l3, lambda s: s.strip() == "for ( sblock = 0; sblock < 3; sblock++ )", "short FFT loop", first=True)
l3[s:s] = [
    f"\t{{ int probe_d_n; {N6} {{ /* probe D: history as stored */",
    "\t    probe_d_r_new[probe_d_n] = r[chn][new][probe_d_n]; probe_d_phi_new[probe_d_n] = phi_sav[chn][new][probe_d_n]; } }",
]
f = find(l3, lambda s: "fft( wsamp_r, wsamp_i, &energy_s[sblock][0], &phi_s[sblock][0], 256 );" in s, "short fft")
l3[f:f] = [
    "\t    { int probe_d_n; for ( probe_d_n = 0; probe_d_n < BLKSIZE_s; probe_d_n++ ) /* probe D */",
    "\t\tprobe_d_wsamp_s[sblock][probe_d_n] = wsamp_r[probe_d_n]; }",
    "\t    probe_d_short_target = probe_d_prefloor_s[sblock]; probe_d_short_armed = 1; /* probe D */",
]
f += 3
l3.insert(f + 1, "\t    probe_d_short_armed = 0; /* probe D */")
c = find(l3, lambda s: s.strip() == "cw[j] = 0.4;", "constant cw")
l3[c + 1 : c + 1] = [
    "\t{ int probe_d_n, probe_d_b; /* probe D: cw before partitioning, short spectra, short window */",
    "\t  for ( probe_d_n = 0; probe_d_n < HBLKSIZE; probe_d_n++ ) probe_d_cw[probe_d_n] = cw[probe_d_n];",
    "\t  for ( probe_d_b = 0; probe_d_b < 3; probe_d_b++ ) for ( probe_d_n = 0; probe_d_n < BLKSIZE_s; probe_d_n++ ) {",
    "\t    probe_d_energy_s[probe_d_b][probe_d_n] = energy_s[probe_d_b][probe_d_n];",
    "\t    probe_d_phi_s[probe_d_b][probe_d_n] = phi_s[probe_d_b][probe_d_n]; }",
    "\t  for ( probe_d_n = 0; probe_d_n < BLKSIZE_s; probe_d_n++ ) probe_d_window_s[probe_d_n] = window_s[probe_d_n]; }",
]
l3_new = l3_nl.join(l3)
open(l3_out, "w", encoding="ascii", newline="").write(l3_new)

# --- subs (on top of probe C) ---
s_text, s_nl, subs = read(subs_src)
i = find(subs, lambda s: s.startswith("FLOAT probe_c_energy_prefloor[BLKSIZE];"), "probe C subs globals")
subs[i + 1 : i + 1] = [
    "/* --- short-FFT probe D (fixture capture only; copies values, writes no encoder variable) --- */",
    "extern int probe_d_short_armed; extern FLOAT *probe_d_short_target;",
]
e = find(subs, lambda s: "probe_c_energy_prefloor[i] = energy[i];" in s, "probe C even copy")
subs.insert(e + 1, "    if (probe_d_short_armed) probe_d_short_target[i] = energy[i]; /* probe D, before floor */")
o = find(subs, lambda s: "probe_c_energy_prefloor[ip] = energy[ip];" in s, "probe C odd copy")
subs.insert(o + 1, "    if (probe_d_short_armed) probe_d_short_target[ip] = energy[ip]; /* probe D */")
r = find(subs, lambda s: "probe C: same reorder" in s, "probe C reorder")
subs.insert(
    r + 1,
    "       if (probe_d_short_armed) { FLOAT probe_d_t = probe_d_short_target[j]; /* probe D: same reorder */"
    " probe_d_short_target[j] = probe_d_short_target[i]; probe_d_short_target[i] = probe_d_t; }",
)
s_new = s_nl.join(subs)
open(subs_out, "w", encoding="ascii", newline="").write(s_new)

for old, new, a, b, nl in (
    (l3_text, l3_new, "l3psy_fft_probe.c", "l3psy_short_probe.c", l3_nl),
    (s_text, s_new, "subs_fft_probe.c", "subs_short_probe.c", s_nl),
):
    diff = difflib.unified_diff(old.split(nl), new.split(nl), a, b, lineterm="", n=1)
    sys.stdout.write("\n".join(diff) + "\n")
