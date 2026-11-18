"""Create probe E ("partition probe") source: observation-only copies of the long partition inputs, built on probe D.

Usage: python make_probe_partition.py <l3psy_short_probe.c> <out l3psy_partition_probe.c>

Probe E is built on probe D (``make_probe_short.py`` output, itself built on probes B and C), so one binary also
reports the earlier probes' fields unchanged. It adds, in ``L3psycho_anal`` right after the partition loop
(``eb[tp] += energy[j]; cb[tp] += cw[j] * energy[j];``, original l3psy.c ll. 553-565) and before the spreading
convolution:
  - eb[0..62] (static double) and cb[0..62] (FLOAT, i.e. float32 under -DUNIX) as accumulated;
  - energy[0..512] (the long post-floor energies the loop read) and cw[0..512] (the unpredictability it read);
  - partition_l[0..512] and numlines[0..62] (the mapping and the table's line counts).
Only probe globals and a block-local counter are written; no encoder variable is assigned. subs.c is unchanged
(probe D's subs_short_probe.c is reused). The unified diff relative to probe D is printed.
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


i = only(lambda s: s.startswith("FLOAT *probe_d_short_target;"), "probe D globals")
lines[i + 1 : i + 1] = [
    "/* --- partition probe E (fixture capture only; copies values, writes no encoder variable) --- */",
    "double probe_e_eb[CBANDS], probe_e_cw[HBLKSIZE]; FLOAT probe_e_cb[CBANDS], probe_e_energy[HBLKSIZE];",
    "int probe_e_partition[HBLKSIZE], probe_e_numlines[CBANDS];",
]
assert only(lambda s: s.strip() == "cb[tp] += cw[j] * energy[j];", "cb accumulation") > 0
c = only(lambda s: "convolve the partitioned energy and unpredictability" in s, "spreading comment")
assert lines[c - 1].strip().startswith("/****"), lines[c - 1]
lines[c - 1 : c - 1] = [
    "\t{ int probe_e_n; /* probe E: partition inputs after accumulation, before spreading */",
    "\t  for ( probe_e_n = 0; probe_e_n < CBANDS; probe_e_n++ ) {",
    "\t    probe_e_eb[probe_e_n] = eb[probe_e_n]; probe_e_cb[probe_e_n] = cb[probe_e_n];",
    "\t    probe_e_numlines[probe_e_n] = numlines[probe_e_n]; }",
    "\t  for ( probe_e_n = 0; probe_e_n < HBLKSIZE; probe_e_n++ ) {",
    "\t    probe_e_energy[probe_e_n] = energy[probe_e_n]; probe_e_cw[probe_e_n] = cw[probe_e_n];",
    "\t    probe_e_partition[probe_e_n] = partition_l[probe_e_n]; } }",
]
new = nl.join(lines)
open(out_path, "w", encoding="ascii", newline="").write(new)
diff = difflib.unified_diff(
    text.split(nl), new.split(nl), "l3psy_short_probe.c", "l3psy_partition_probe.c", lineterm="", n=1
)
sys.stdout.write("\n".join(diff) + "\n")
