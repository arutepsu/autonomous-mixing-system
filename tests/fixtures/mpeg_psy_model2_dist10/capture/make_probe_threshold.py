"""Create probe G ("threshold probe") source: observation-only copies of the tonality, SNR and partition thresholds, on F.

Usage: python make_probe_threshold.py <l3psy_spreading_probe.c> <out l3psy_threshold_probe.c>

Probe G is built on probe F (``make_probe_spreading.py`` output), so one binary also reports the earlier probes' fields
unchanged. It adds, in the Layer III long path of ``L3psycho_anal`` (original l3psy.c ll. 591-617):
  - in the tonality loop: ``tbb`` right after ``tbb = -0.299 - 0.43*cbb`` (raw tonality factor), after the clamp
    ``tbb = minimum( 1.0, maximum( 0.0, tbb) )`` and ``SNR_l[b]`` after ``maximum( minval[b], 29 tbb + 6 (1 - tbb) )``;
  - after ``nb[b] = ecb[b] * norm_l[b] * exp( -SNR_l[b] * LN_TO_LOG10 )``: nb[0..62] (FLOAT work array);
  - in the pre-echo loop: nb_1[chn][b] and nb_2[chn][b] as read (before the update), ``temp_1`` and ``thr[b]``, then
    after the loop the history values the call stored;
  - the 44.1 kHz tables minval[], qthr_l[] and norm_l[] (static double, filled by L3para_read).
Only probe globals are written; no encoder variable is assigned. subs.c is unchanged (probe D's).
The unified diff relative to probe F is printed.
"""

import difflib
import sys

src, out_path = sys.argv[1:3]
text = open(src, encoding="ascii", newline="").read()
nl = "\r\n" if "\r\n" in text else "\n"
lines = text.split(nl)


def only(predicate, what: str) -> int:
    hits = [n for n, line in enumerate(lines) if predicate(line)]
    assert len(hits) == 1, (what, hits)
    return hits[0]


i = only(lambda s: s.startswith("double probe_f_log_ratio[CBANDS];"), "probe F globals")
lines[i + 1 : i + 1] = [
    "/* --- threshold probe G (fixture capture only; copies values, writes no encoder variable) --- */",
    "double probe_g_tbb_raw[CBANDS], probe_g_tbb[CBANDS], probe_g_snr[CBANDS], probe_g_nb_1[CBANDS];",
    "double probe_g_nb_2[CBANDS], probe_g_temp1[CBANDS], probe_g_thr[CBANDS], probe_g_nb_1_after[CBANDS];",
    "double probe_g_nb_2_after[CBANDS], probe_g_minval[CBANDS], probe_g_qthr[CBANDS], probe_g_norm[CBANDS];",
    "FLOAT probe_g_nb[CBANDS];",
]
# 1. tonality loop: raw factor, clamped factor, SNR
u = only(lambda s: s.strip().startswith("tbb = -0.299 - 0.43*cbb;"), "raw tonality")
lines.insert(u + 1, "\t    probe_g_tbb_raw[b] = tbb; /* probe G */")
c = only(lambda s: s.strip().startswith("tbb = minimum( 1.0, maximum( 0.0, tbb) ) ;"), "tonality clamp")
lines.insert(c + 1, "\t    probe_g_tbb[b] = tbb; /* probe G */")
s = only(lambda s: s.strip().startswith("SNR_l[b] = maximum( minval[b], 29.0*tbb+6.0*(1.0-tbb) );"), "SNR")
lines.insert(s + 1, "\t    probe_g_snr[b] = SNR_l[b]; /* probe G */")
# 2. after the nb loop (one statement, no braces), before the pre-echo loop
n = only(lambda s: s.strip() == "nb[b] = ecb[b] * norm_l[b] * exp( -SNR_l[b] * LN_TO_LOG10 );", "nb")
lines[n + 1 : n + 1] = [
    "\t{ int probe_g_n; /* probe G: unbounded threshold, history as read, tables */",
    "\t  for ( probe_g_n = 0; probe_g_n < CBANDS; probe_g_n++ ) {",
    "\t    probe_g_nb[probe_g_n] = nb[probe_g_n];",
    "\t    probe_g_nb_1[probe_g_n] = nb_1[chn][probe_g_n]; probe_g_nb_2[probe_g_n] = nb_2[chn][probe_g_n];",
    "\t    probe_g_minval[probe_g_n] = minval[probe_g_n]; probe_g_qthr[probe_g_n] = qthr_l[probe_g_n];",
    "\t    probe_g_norm[probe_g_n] = norm_l[probe_g_n]; } }",
]
# 3. pre-echo loop: limit and final threshold, then the stored history after the loop
t = only(lambda s: s.strip().startswith("thr[b] = maximum( qthr_l[b], temp_1 );"), "final threshold")
lines.insert(t + 1, "\t    probe_g_temp1[b] = temp_1; probe_g_thr[b] = thr[b]; /* probe G */")
h = only(lambda s: s.strip() == "nb_1[chn][b] = nb[b];", "history update")
assert lines[h + 1].strip() == "}", lines[h + 1]
lines[h + 2 : h + 2] = [
    "\t{ int probe_g_n; /* probe G: history as stored by this call */",
    "\t  for ( probe_g_n = 0; probe_g_n < CBANDS; probe_g_n++ ) {",
    "\t    probe_g_nb_1_after[probe_g_n] = nb_1[chn][probe_g_n]; probe_g_nb_2_after[probe_g_n] = nb_2[chn][probe_g_n]; } }",
]
new = nl.join(lines)
open(out_path, "w", encoding="ascii", newline="").write(new)
diff = difflib.unified_diff(
    text.split(nl), new.split(nl), "l3psy_spreading_probe.c", "l3psy_threshold_probe.c", lineterm="", n=1
)
sys.stdout.write("\n".join(diff) + "\n")
