/* Fixture harness for probe B (long-band mapping probe): call the dist10 L3psycho_anal, built from
 * l3psy_mapping_probe.c, on one mono int16 stream of any length.
 *
 * Usage: harness_mapping <sfreq Hz> <raw int16 little-endian mono file> > out.jsonl
 * The clip of N samples is cut into ceil(N / 576) granules; a final partial granule is filled with zeros, as the
 * dist10 encoder does with a short final frame (encode.c read_samples, ll. 160-162). Each call passes one granule
 * for chn = 0; the save buffer starts zeroed. After the call, savebuf[0..1023] holds exactly the long-FFT input
 * of that call, stream samples [576k - 768, 576k + 256), with zeros before sample 0 and after sample N - 1.
 * One JSON line per call: call index, pe, the encoder's branch (pe < switch_pe = 1800), the window bounds, whether
 * the window ends inside the clip, the count of nonzero int16 samples in the window (exact digital silence is 0),
 * and probe B's 21 long-band values of this call (en, thm, thm_quiet, thm_from_quiet_partitions, thm_signal).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "common.h"
#include "encoder.h"
#include "l3psy.h"

extern double probe_b_en[SBMAX_l], probe_b_thm[SBMAX_l], probe_b_thm_quiet[SBMAX_l];
extern double probe_b_thm_from_quiet[SBMAX_l], probe_b_thm_signal[SBMAX_l];

static void print_array(const char *name, const double *v, int n) {
    int i;
    printf(",\"%s\":[", name);
    for (i = 0; i < n; i++) printf("%s%.17g", i ? "," : "", v[i]);
    printf("]");
}

int main(int argc, char **argv) {
    static short savebuf[1344];
    short *clip, granule[576];
    FLOAT snr32[32];
    double ratio_d[21], ratio_ds[12][3], pe, sfreq;
    gr_info cod_info;
    FILE *in;
    long n_samples, calls, call, start, end;
    int i, nonzero;

    if (argc != 3) { fprintf(stderr, "usage: harness_mapping sfreq file\n"); return 2; }
    sfreq = atof(argv[1]);
    in = fopen(argv[2], "rb");
    if (!in) { perror("open"); return 2; }
    fseek(in, 0, SEEK_END);
    n_samples = ftell(in) / (long) sizeof(short);
    if (ftell(in) % (long) sizeof(short) != 0) { fprintf(stderr, "odd byte count\n"); return 3; }
    fseek(in, 0, SEEK_SET);
    calls = (n_samples + 575) / 576;
    clip = calloc((size_t) (calls * 576), sizeof(short));
    if (!clip || fread(clip, sizeof(short), (size_t) n_samples, in) != (size_t) n_samples) {
        fprintf(stderr, "read failed\n");
        return 2;
    }
    fclose(in);
    memset(savebuf, 0, sizeof(savebuf));
    memset(&cod_info, 0, sizeof(cod_info));
    for (call = 0; call < calls; call++) {
        memcpy(granule, clip + call * 576, sizeof(granule));
        L3psycho_anal(granule, savebuf, 0, 3, snr32, sfreq, ratio_d, ratio_ds, &pe, &cod_info);
        nonzero = 0;
        for (i = 0; i < 1024; i++) nonzero += savebuf[i] != 0;
        start = 576 * call - 768;
        end = 576 * call + 256;
        printf("{\"call\":%ld,\"pe\":%.17g,\"encoder_long_path\":%d,\"clip_samples\":%ld,", call, pe,
               pe < 1800 ? 1 : 0, n_samples);
        printf("\"window_start\":%ld,\"window_end\":%ld,\"window_end_within_clip\":%d,", start, end,
               end <= n_samples ? 1 : 0);
        printf("\"window_nonzero_samples\":%d", nonzero);
        print_array("en", probe_b_en, SBMAX_l);
        print_array("thm", probe_b_thm, SBMAX_l);
        print_array("thm_quiet", probe_b_thm_quiet, SBMAX_l);
        print_array("thm_from_quiet_partitions", probe_b_thm_from_quiet, SBMAX_l);
        print_array("thm_signal", probe_b_thm_signal, SBMAX_l);
        printf("}\n");
    }
    free(clip);
    return 0;
}
