/* Fixture harness: call the unmodified dist10 L3psycho_anal (plus observation probe) on one mono int16 stream.
 *
 * Usage: harness <sfreq Hz> <raw int16 little-endian mono file> > out.jsonl
 * Each call consumes 576 new samples (one granule), exactly as musicin.c passes &buffer[ch][gr*576] for one
 * channel (chn = 0). The save buffer starts zeroed, as in musicin.c (memset of sam). One JSON line per call:
 * call index, pe, returned long ratio (which is the previous call's analysis), whether the current call took
 * the long-block path, and the current call's en[sb] and thm[sb] (21 long scale-factor bands).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "common.h"
#include "encoder.h"
#include "l3psy.h"

extern double probe_en[SBMAX_l], probe_thm[SBMAX_l];
extern int probe_long_valid;

static void print_array(const char *name, const double *v, int n) {
    int i;
    printf("\"%s\":[", name);
    for (i = 0; i < n; i++) printf("%s%.17g", i ? "," : "", v[i]);
    printf("]");
}

int main(int argc, char **argv) {
    static short savebuf[1344];
    short granule[576];
    FLOAT snr32[32];
    double ratio_d[21], ratio_ds[12][3], pe;
    gr_info cod_info;
    double sfreq;
    FILE *in;
    int call = 0;
    size_t got;

    if (argc != 3) { fprintf(stderr, "usage: harness sfreq file\n"); return 2; }
    sfreq = atof(argv[1]);
    in = fopen(argv[2], "rb");
    if (!in) { perror("open"); return 2; }
    memset(savebuf, 0, sizeof(savebuf));
    memset(&cod_info, 0, sizeof(cod_info));
    while ((got = fread(granule, sizeof(short), 576, in)) == 576) {
        L3psycho_anal(granule, savebuf, 0, 3, snr32, sfreq, ratio_d, ratio_ds, &pe, &cod_info);
        printf("{\"call\":%d,\"pe\":%.17g,\"block_type_reported\":%d,\"long_path\":%d,", call, pe,
               (int) cod_info.block_type, probe_long_valid);
        print_array("returned_ratio_l", ratio_d, 21);
        printf(",");
        if (probe_long_valid) {
            print_array("en", probe_en, SBMAX_l);
            printf(",");
            print_array("thm", probe_thm, SBMAX_l);
        } else {
            printf("\"en\":null,\"thm\":null");
        }
        printf("}\n");
        call++;
    }
    if (got != 0) { fprintf(stderr, "trailing %zu samples ignored\n", got); return 3; }
    fclose(in);
    return 0;
}
