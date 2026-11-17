"""Create probe H ("band-mapping input probe") source: observation-only copies of the long-band mapping inputs, on G.

Usage: python make_probe_band.py <l3psy_threshold_probe.c> <out l3psy_band_probe.c>

Probe H is built on probe G (``make_probe_threshold.py`` output), so one binary also reports the earlier probes'
fields unchanged. Right before probe B's long-band mapping (which applies the encoder's expressions of original
l3psy.c ll. 654-660 on every call, after ``pe`` and before the block-switching branch), it copies:
  - eb[0..62] (static double) and thr[0..62] (local double: the final threshold after the pre-echo limit and the
    quiet floor) exactly as that mapping reads them;
  - the long scale-factor-band tables cbw_l, bu_l, bo_l (int) and w1_l, w2_l (static double) from L3para_read.
Only probe globals are written; no encoder variable is assigned. subs.c is unchanged (probe D's).
The unified diff relative to probe G is printed.
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


i = only(lambda s: s.startswith("FLOAT probe_g_nb[CBANDS];"), "probe G globals")
lines[i + 1 : i + 1] = [
    "/* --- band-mapping input probe H (fixture capture only; copies values, writes no encoder variable) --- */",
    "double probe_h_eb[CBANDS], probe_h_thr[CBANDS], probe_h_w1[SBMAX_l], probe_h_w2[SBMAX_l];",
    "int probe_h_cbw[SBMAX_l], probe_h_bu[SBMAX_l], probe_h_bo[SBMAX_l];",
]
m = only(lambda s: "probe B: encoder long-band mapping" in s, "probe B mapping")
lines[m:m] = [
    "\t{ int probe_h_n; /* probe H: the long-band mapping inputs and tables, as probe B's mapping reads them */",
    "\t  for ( probe_h_n = 0; probe_h_n < CBANDS; probe_h_n++ ) {",
    "\t    probe_h_eb[probe_h_n] = eb[probe_h_n]; probe_h_thr[probe_h_n] = thr[probe_h_n]; }",
    "\t  for ( probe_h_n = 0; probe_h_n < SBMAX_l; probe_h_n++ ) {",
    "\t    probe_h_w1[probe_h_n] = w1_l[probe_h_n]; probe_h_w2[probe_h_n] = w2_l[probe_h_n];",
    "\t    probe_h_cbw[probe_h_n] = cbw_l[probe_h_n]; probe_h_bu[probe_h_n] = bu_l[probe_h_n];",
    "\t    probe_h_bo[probe_h_n] = bo_l[probe_h_n]; } }",
]
new = nl.join(lines)
open(out_path, "w", encoding="ascii", newline="").write(new)
diff = difflib.unified_diff(
    text.split(nl), new.split(nl), "l3psy_threshold_probe.c", "l3psy_band_probe.c", lineterm="", n=1
)
sys.stdout.write("\n".join(diff) + "\n")
