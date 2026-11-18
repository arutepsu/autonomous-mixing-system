"""Create probe F ("spreading probe") source: observation-only copies of the spreading and tonality inputs, on probe E.

Usage: python make_probe_spreading.py <l3psy_partition_probe.c> <out l3psy_spreading_probe.c>

Probe F is built on probe E (``make_probe_partition.py`` output), so one binary also reports the earlier probes'
fields unchanged. It adds, in the Layer III long path of ``L3psycho_anal`` (original l3psy.c ll. 573-604):
  - after the convolution ``ecb[b] += s3_l[b][k] * eb[k]; ctb[b] += s3_l[b][k] * cb[k];``: copies of ecb[0..62]
    (FLOAT, i.e. float32) and ctb[0..62] (static double), and of the spreading matrix s3_l[63][63] (static double);
  - inside the tonality loop: the ratio ``cbb = ctb[b] / ecb[b]`` right after the division, the value after the
    lower clamp ``if (cbb < 0.01) cbb = 0.01``, whether the division ran (ecb[b] != 0) and the value the loop passes
    on to ``tbb = -0.299 - 0.43 * cbb`` (log of the clamped ratio, or 0.0 where the division was skipped).
Only probe globals and a block-local counter are written; no encoder variable is assigned. subs.c is unchanged.
The unified diff relative to probe E is printed.
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


i = only(lambda s: s.startswith("int probe_e_partition[HBLKSIZE]"), "probe E globals")
lines[i + 1 : i + 1] = [
    "/* --- spreading probe F (fixture capture only; copies values, writes no encoder variable) --- */",
    "double probe_f_ctb[CBANDS], probe_f_s3[CBANDS][CBANDS], probe_f_ratio[CBANDS], probe_f_ratio_clamped[CBANDS];",
    "double probe_f_log_ratio[CBANDS]; FLOAT probe_f_ecb[CBANDS]; int probe_f_divided[CBANDS];",
]
# 1. after the convolution loop, before the tonality comment
t = only(lambda s: "calculate the tonality of each threshold calculation partition" in s, "tonality comment")
lines[t:t] = [
    "\t{ int probe_f_n, probe_f_m; /* probe F: spread values after the convolution, and the spreading matrix */",
    "\t  for ( probe_f_n = 0; probe_f_n < CBANDS; probe_f_n++ ) {",
    "\t    probe_f_ecb[probe_f_n] = ecb[probe_f_n]; probe_f_ctb[probe_f_n] = ctb[probe_f_n];",
    "\t    probe_f_divided[probe_f_n] = 0; probe_f_ratio[probe_f_n] = 0.0; probe_f_ratio_clamped[probe_f_n] = 0.0;",
    "\t    for ( probe_f_m = 0; probe_f_m < CBANDS; probe_f_m++ )",
    "\t      probe_f_s3[probe_f_n][probe_f_m] = s3_l[probe_f_n][probe_f_m]; } }",
]
# 2. inside the tonality loop
d = only(lambda s: s.strip() == "cbb = ctb[b]/ecb[b];", "division")
lines.insert(d + 1, "\t\tprobe_f_ratio[b] = cbb; probe_f_divided[b] = 1; /* probe F */")
c = only(lambda s: s.strip() == "if (cbb <0.01) cbb = 0.01;", "lower clamp")
lines.insert(c + 1, "\t\tprobe_f_ratio_clamped[b] = cbb; /* probe F */")
u = only(lambda s: s.strip().startswith("tbb = -0.299 - 0.43*cbb;"), "tonality use")
lines.insert(u, "\t    probe_f_log_ratio[b] = cbb; /* probe F: value passed to tbb */")
new = nl.join(lines)
open(out_path, "w", encoding="ascii", newline="").write(new)
diff = difflib.unified_diff(
    text.split(nl), new.split(nl), "l3psy_partition_probe.c", "l3psy_spreading_probe.c", lineterm="", n=1
)
sys.stdout.write("\n".join(diff) + "\n")
