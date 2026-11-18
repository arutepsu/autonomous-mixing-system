"""Create l3psy_probe.c: the original dist10 l3psy.c plus observation-only copies of en/thm (no computation change).

Usage: python make_probe.py <original l3psy.c> <output l3psy_probe.c>
The inserted lines only copy already-computed values into globals and set a flag; they are printed as a
unified diff so the modification can be recorded with the fixture.
"""

import difflib
import sys

source, target = sys.argv[1], sys.argv[2]
text = open(source, encoding="ascii", newline="").read()
nl = "\r\n" if "\r\n" in text else "\n"
lines = text.split(nl)

# 1. Globals after the include block.
anchor = '#include "l3side.h"'
i = lines.index(anchor)
globals_ = [
    "/* --- observation probe (fixture capture only; no computation change) --- */",
    "double probe_en[SBMAX_l], probe_thm[SBMAX_l];",
    "int probe_long_valid;",
]
lines[i + 1 : i + 1] = globals_

# 2. Reset the flag at the start of each Layer III analysis (just before the delay-buffer shift).
k = next(n for n, line in enumerate(lines) if "Delay signal by sync_flush=768 samples" in line)
lines.insert(k - 1, "\tprobe_long_valid = 0; /* probe */")

# 3. Copy en/thm after the long-block scale-factor loop (inside the no-attack branch).
j = next(n for n, line in enumerate(lines) if "ratio[chn][sb] = 0.0;" in line)
assert lines[j + 1].strip() == "}", lines[j + 1]
lines.insert(
    j + 2,
    "\t    for ( sb = 0; sb < SBMAX_l; sb++ ) { probe_en[sb] = en[sb]; probe_thm[sb] = thm[sb]; }"
    " probe_long_valid = 1; /* probe */",
)

out = nl.join(lines)
open(target, "w", encoding="ascii", newline="").write(out)
sys.stdout.write(
    "\n".join(difflib.unified_diff(text.split(nl), out.split(nl), "l3psy.c", "l3psy_probe.c", lineterm="", n=1)) + "\n"
)
