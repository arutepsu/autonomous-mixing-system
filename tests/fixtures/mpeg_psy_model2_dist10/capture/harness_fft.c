/* Fixture harness for probe C (long-FFT probe): dist10 L3psycho_anal built from l3psy_fft_probe.c and
 * subs_fft_probe.c, on one mono int16 stream of any length (framing as harness_mapping.c).
 *
 * Usage: harness_fft <sfreq Hz> <raw int16 LE mono file> <selected calls: comma list or -> <window out> > out.jsonl
 * Every call prints the same probe B fields as harness_mapping.c, in the same format, so the line can be compared
 * with expected_long_mapping. Selected calls additionally print the long-FFT stage of that call, 1024 values each,
 * index = natural FFT bin (or window position for windowed_samples):
 *   windowed_samples  wsamp_r as passed into fft() (window[j] * savebuf[j], FLOAT)
 *   energy_prefloor   |X|^2 as computed in fft()'s final stage, before the 0.0005 floor test (FLOAT)
 *   energy, phi       energy[] and phi[] as returned to L3psycho_anal (FLOAT)
 * FLOAT is float under -DUNIX; FLOAT values are printed with %.9g, which round-trips float32 exactly.
 * The Hann coefficients window[0..1023] are written once, after the first call, to <window out> as JSON.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "common.h"
#include "encoder.h"
#include "l3psy.h"

extern double probe_b_en[SBMAX_l], probe_b_thm[SBMAX_l], probe_b_thm_quiet[SBMAX_l];
extern double probe_b_thm_from_quiet[SBMAX_l], probe_b_thm_signal[SBMAX_l];
extern FLOAT probe_c_window[BLKSIZE], probe_c_wsamp[BLKSIZE], probe_c_energy[BLKSIZE], probe_c_phi[BLKSIZE];
extern FLOAT probe_c_energy_prefloor[BLKSIZE];

static void print_array(const char *name, const double *v, int n) {
    int i;
    printf(",\"%s\":[", name);
    for (i = 0; i < n; i++) printf("%s%.17g", i ? "," : "", v[i]);
    printf("]");
}

static void print_floats(FILE *out, const char *name, const FLOAT *v, int n) {
    int i;
    fprintf(out, ",\"%s\":[", name);
    for (i = 0; i < n; i++) fprintf(out, "%s%.9g", i ? "," : "", (double) v[i]);
    fprintf(out, "]");
}

static int selected(const char *list, long call) {
    char buf[256], *tok;
    if (strcmp(list, "-") == 0) return 0;
    strncpy(buf, list, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = 0;
    for (tok = strtok(buf, ","); tok; tok = strtok(NULL, ","))
        if (atol(tok) == call) return 1;
    return 0;
}

int main(int argc, char **argv) {
    static short savebuf[1344];
    short *clip, granule[576];
    FLOAT snr32[32];
    double ratio_d[21], ratio_ds[12][3], pe, sfreq;
    gr_info cod_info;
    FILE *in, *wout;
    long n_samples, calls, call, start, end;
    int i, nonzero;

    if (argc != 5) { fprintf(stderr, "usage: harness_fft sfreq file calls window_out\n"); return 2; }
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
        if (call == 0) {
            wout = fopen(argv[4], "w");
            if (!wout) { perror("window out"); return 2; }
            fprintf(wout, "{\"dtype\":\"float32\"");
            print_floats(wout, "window", probe_c_window, BLKSIZE);
            fprintf(wout, "}\n");
            fclose(wout);
        }
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
        if (selected(argv[3], call)) {
            print_floats(stdout, "windowed_samples", probe_c_wsamp, BLKSIZE);
            print_floats(stdout, "energy_prefloor", probe_c_energy_prefloor, BLKSIZE);
            print_floats(stdout, "energy", probe_c_energy, BLKSIZE);
            print_floats(stdout, "phi", probe_c_phi, BLKSIZE);
        }
        printf("}\n");
    }
    free(clip);
    return 0;
}
