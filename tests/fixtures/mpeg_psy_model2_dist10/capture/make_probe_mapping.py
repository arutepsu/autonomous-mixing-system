"""Create l3psy_mapping_probe.c: the original dist10 l3psy.c plus a separately labelled "long-band mapping probe".

Usage: python make_probe_mapping.py <original l3psy.c> <output l3psy_mapping_probe.c>

Probe B (this script) differs from the encoder-path probe A (make_probe.py). Probe A copies en/thm only where the
encoder itself computes them (pe < switch_pe). Probe B evaluates the encoder's own long-band mapping
(l3psy.c ll. 652-660: same expressions, same summation order) on the partition values eb[b] and thr[b] of
*every* call, at the point after perceptual entropy is computed and before the block-switching branch, which on
short-block calls overwrites eb and thr with short-block values. On long-block calls probe B must therefore
reproduce probe A bit for bit; on short-block calls it yields values the encoder never forms (a project
extension, not encoder behaviour).

Probe B also exposes, per band, the same mapping applied to
  - qthr_l[b] (threshold in quiet):                                   thm_quiet
  - thr[b] restricted to partitions where qthr_l[b] >= temp_1, i.e.
    where threshold in quiet decided thr[b] (ties counted as quiet):  thm_from_quiet_partitions
  - temp_1 = min(nb, 2 nb_1, 16 nb_2) (pre-echo-limited signal threshold before the threshold-in-quiet max):
                                                                      thm_signal
The inserted lines only read already-computed values; no encoder variable is written. The unified diff is
printed so the modification can be recorded with the fixture.
"""

import difflib
import sys

source, target = sys.argv[1], sys.argv[2]
text = open(source, encoding="ascii", newline="").read()
nl = "\r\n" if "\r\n" in text else "\n"
lines = text.split(nl)


def index_of(predicate, what):
    hits = [n for n, line in enumerate(lines) if predicate(line)]
    assert len(hits) == 1, (what, hits)
    return hits[0]


# 1. Globals after the include block.
i = lines.index('#include "l3side.h"')
lines[i + 1 : i + 1] = [
    "/* --- long-band mapping probe B (fixture capture only; reads values, writes no encoder variable) --- */",
    "double probe_b_en[SBMAX_l], probe_b_thm[SBMAX_l], probe_b_thm_quiet[SBMAX_l];",
    "double probe_b_thm_from_quiet[SBMAX_l], probe_b_thm_signal[SBMAX_l];",
    "static double probe_b_temp1[CBANDS];",
]

# 2. Record temp_1 per partition inside the pre-echo loop (right after thr[b] is set from it).
j = index_of(lambda s: "thr[b] = maximum( qthr_l[b], temp_1 );" in s, "thr assignment")
lines.insert(j + 1, "\t    probe_b_temp1[b] = temp_1; /* probe B */")

# 3. Long-band mapping of every call, just before the block-switching decision.
k = index_of(lambda s: s.startswith("#define switch_pe"), "switch_pe")
lines[k:k] = [
    "\t/* probe B: encoder long-band mapping (ll. 654-660 expressions and order) on this call's eb/thr */",
    "\tfor ( sb = 0; sb < SBMAX_l; sb++ )",
    "\t{",
    "\t    probe_b_en[sb] = w1_l[sb] * eb[bu_l[sb]] + w2_l[sb] * eb[bo_l[sb]];",
    "\t    probe_b_thm[sb] = w1_l[sb] *thr[bu_l[sb]] + w2_l[sb] * thr[bo_l[sb]];",
    "\t    probe_b_thm_quiet[sb] = w1_l[sb] * qthr_l[bu_l[sb]] + w2_l[sb] * qthr_l[bo_l[sb]];",
    "\t    probe_b_thm_signal[sb] = w1_l[sb] * probe_b_temp1[bu_l[sb]] + w2_l[sb] * probe_b_temp1[bo_l[sb]];",
    "\t    probe_b_thm_from_quiet[sb] =",
    "\t\tw1_l[sb] * (qthr_l[bu_l[sb]] >= probe_b_temp1[bu_l[sb]] ? thr[bu_l[sb]] : 0.0)",
    "\t\t+ w2_l[sb] * (qthr_l[bo_l[sb]] >= probe_b_temp1[bo_l[sb]] ? thr[bo_l[sb]] : 0.0);",
    "\t    for ( b = bu_l[sb]+1; b < bo_l[sb]; b++ )",
    "\t    {",
    "\t\tprobe_b_en[sb]  += eb[b];",
    "\t\tprobe_b_thm[sb] += thr[b];",
    "\t\tprobe_b_thm_quiet[sb] += qthr_l[b];",
    "\t\tprobe_b_thm_signal[sb] += probe_b_temp1[b];",
    "\t\tif ( qthr_l[b] >= probe_b_temp1[b] ) probe_b_thm_from_quiet[sb] += thr[b];",
    "\t    }",
    "\t}",
]

out = nl.join(lines)
open(target, "w", encoding="ascii", newline="").write(out)
sys.stdout.write(
    "\n".join(difflib.unified_diff(text.split(nl), out.split(nl), "l3psy.c", "l3psy_mapping_probe.c", lineterm="", n=1))
    + "\n"
)
