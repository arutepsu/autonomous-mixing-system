/* Fixture harness for probe G (threshold probe): dist10 L3psycho_anal built from l3psy_threshold_probe.c and
 * subs_short_probe.c (probes B + C + D + E + F + G), on one mono int16 stream of any length.
 *
 * Usage: harness_threshold <sfreq Hz> <raw int16 LE mono file> <probe C calls|-> <probe D calls|-> <window_s out>
 *        <probe E calls|-> <mapping out> <probe F calls|-> <s3 out> <probe G calls|-> <tables out>
 * Lines are exactly harness_spreading.c's for the same probe C, D, E and F call lists, followed on probe G calls by
 *   g_ecb              63  ecb[] after the convolution (FLOAT, %.9g; the value nb is formed from)
 *   g_tonality_input   63  value passed to tbb (double): log(clamped ctb/ecb), or 0.0 where not divided
 *   tonality_raw       63  tbb = -0.299 - 0.43 * cbb, before the clamp (double)
 *   tonality           63  tbb after minimum( 1.0, maximum( 0.0, tbb) ) (double)
 *   snr                63  SNR_l[b] = maximum( minval[b], 29 tbb + 6 (1 - tbb) ) (double, dB)
 *   nb                 63  nb[b] = ecb[b] * norm_l[b] * exp( -SNR_l[b] * LN_TO_LOG10 ) (FLOAT, %.9g)
 *   nb_1_before        63  nb_1[chn][b] as read by the pre-echo loop (double)
 *   nb_2_before        63  nb_2[chn][b] as read by the pre-echo loop (double)
 *   pre_echo_limit     63  temp_1 = minimum( nb, minimum( 2 nb_1, 16 nb_2 ) ) (double)
 *   thr                63  thr[b] = maximum( qthr_l[b], temp_1 ), before band mapping (double)
 *   nb_1_after         63  nb_1[chn][b] after the update (double)
 *   nb_2_after         63  nb_2[chn][b] after the update (double)
 * The tables minval[], qthr_l[] and norm_l[] (double, %.17g) are written once, after the first call, to <tables out>.
 * The probe F description of harness_spreading.c follows.
 * Lines are exactly harness_partition.c's for the same probe C, D and E call lists, followed on probe F calls by
 *   ecb                63  ecb[] after the convolution (FLOAT, %.9g)
 *   ctb                63  ctb[] after the convolution (double, %.17g)
 *   ratio_divided      63  1 where the tonality loop divided (ecb[b] != 0), else 0
 *   ratio              63  ctb[b] / ecb[b] right after the division (double; 0 where not divided)
 *   ratio_clamped      63  after the lower clamp to 0.01 (double; 0 where not divided)
 *   tonality_input     63  value passed to tbb = -0.299 - 0.43 * cbb: log(clamped ratio), or 0.0 (double)
 * The spreading matrix s3_l[63][63] (double, %.17g, row = output partition b, column = input partition k) is
 * written once, after the first call, to <s3 out>.
 * The probe E description of harness_partition.c follows.
 * Lines are exactly harness_short.c's for the same probe C and D call lists, followed on probe E calls by
 *   partition_energy  513  energy[] as read by the partition loop (FLOAT, %.9g)
 *   partition_cw      513  cw[] as read by the partition loop (double, %.17g)
 *   eb                 63  eb[] after accumulation, before spreading (double, %.17g)
 *   cb                 63  cb[] after accumulation, before spreading (FLOAT, %.9g)
 * The mapping partition_l[0..512] and numlines[0..62] are written once, after the first call, to <mapping out>.
 * The probe D description of harness_short.c follows.
 * Every line starts exactly like a harness_fft.c line for the same probe C call list (probe B fields, then the
 * probe C arrays on probe C calls), so with probe D fields removed it can be compared byte for byte with
 * expected_long_fft / expected_long_mapping. Probe D calls append:
 *   short_windowed_samples  3 x 256  wsamp_r passed to each short fft() (window_s[j] * savebuf[128 (2 + s) + j])
 *   short_energy_prefloor   3 x 129  energy per natural short bin 0..128 before the 0.0005 floor test
 *   short_energy, short_phi 3 x 129  energy_s / phi_s as used by the predictor (bins 129..255 are mirrors)
 *   long_history            ages new/old/oldest and r / phi_sav for bins 0..5: old and oldest before the
 *                           long predictor loop, new after it
 *   cw                      513 unpredictability values before partitioning (double, %.17g)
 * FLOAT is float under -DUNIX; FLOAT values are printed with %.9g (exact float32 round trip).
 * The short Hann coefficients window_s[0..255] are written once, after the first call, to <window_s out>.
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
extern FLOAT probe_d_window_s[BLKSIZE_s], probe_d_wsamp_s[3][BLKSIZE_s], probe_d_prefloor_s[3][BLKSIZE_s];
extern FLOAT probe_d_energy_s[3][BLKSIZE_s], probe_d_phi_s[3][BLKSIZE_s];
extern FLOAT probe_d_r_old[6], probe_d_r_oldest[6], probe_d_phi_old[6], probe_d_phi_oldest[6];
extern FLOAT probe_d_r_new[6], probe_d_phi_new[6];
extern double probe_d_cw[HBLKSIZE];
extern int probe_d_age_new, probe_d_age_old, probe_d_age_oldest;
extern double probe_e_eb[CBANDS], probe_e_cw[HBLKSIZE];
extern FLOAT probe_e_cb[CBANDS], probe_e_energy[HBLKSIZE];
extern int probe_e_partition[HBLKSIZE], probe_e_numlines[CBANDS];
extern double probe_f_ctb[CBANDS], probe_f_s3[CBANDS][CBANDS], probe_f_ratio[CBANDS], probe_f_ratio_clamped[CBANDS];
extern double probe_f_log_ratio[CBANDS];
extern FLOAT probe_f_ecb[CBANDS];
extern int probe_f_divided[CBANDS];
extern double probe_g_tbb_raw[CBANDS], probe_g_tbb[CBANDS], probe_g_snr[CBANDS], probe_g_nb_1[CBANDS];
extern double probe_g_nb_2[CBANDS], probe_g_temp1[CBANDS], probe_g_thr[CBANDS], probe_g_nb_1_after[CBANDS];
extern double probe_g_nb_2_after[CBANDS], probe_g_minval[CBANDS], probe_g_qthr[CBANDS], probe_g_norm[CBANDS];
extern FLOAT probe_g_nb[CBANDS];

static void print_array(const char *name, const double *v, int n) {
    int i;
    printf(",\"%s\":[", name);
    for (i = 0; i < n; i++) printf("%s%.17g", i ? "," : "", v[i]);
    printf("]");
}

static void print_array_to(FILE *out, const char *name, const double *v, int n, int first) {
    int i;
    fprintf(out, "%s\"%s\":[", first ? "" : ",", name);
    for (i = 0; i < n; i++) fprintf(out, "%s%.17g", i ? "," : "", v[i]);
    fprintf(out, "]");
}

static void print_floats(FILE *out, const char *name, const FLOAT *v, int n) {
    int i;
    fprintf(out, ",\"%s\":[", name);
    for (i = 0; i < n; i++) fprintf(out, "%s%.9g", i ? "," : "", (double) v[i]);
    fprintf(out, "]");
}

static void print_rows(const char *name, FLOAT rows[3][BLKSIZE_s], int n) {
    int s, i;
    printf(",\"%s\":[", name);
    for (s = 0; s < 3; s++) {
        printf("%s[", s ? "," : "");
        for (i = 0; i < n; i++) printf("%s%.9g", i ? "," : "", (double) rows[s][i]);
        printf("]");
    }
    printf("]");
}

static void print_six(const char *name, const FLOAT *v) {
    int i;
    printf("%s\"%s\":[", strcmp(name, "r_old") ? "," : "", name);
    for (i = 0; i < 6; i++) printf("%s%.9g", i ? "," : "", (double) v[i]);
    printf("]");
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

    if (argc != 12) {
        fprintf(stderr, "usage: harness_threshold sfreq file c_calls d_calls window_s_out e_calls mapping_out "
                        "f_calls s3_out g_calls tables_out\n");
        return 2;
    }
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
            wout = fopen(argv[5], "w");
            if (!wout) { perror("window_s out"); return 2; }
            fprintf(wout, "{\"dtype\":\"float32\"");
            print_floats(wout, "window_s", probe_d_window_s, BLKSIZE_s);
            fprintf(wout, "}\n");
            fclose(wout);
            wout = fopen(argv[7], "w");
            if (!wout) { perror("mapping out"); return 2; }
            fprintf(wout, "{\"partition_l\":[");
            for (i = 0; i < HBLKSIZE; i++) fprintf(wout, "%s%d", i ? "," : "", probe_e_partition[i]);
            fprintf(wout, "],\"numlines\":[");
            for (i = 0; i < CBANDS; i++) fprintf(wout, "%s%d", i ? "," : "", probe_e_numlines[i]);
            fprintf(wout, "]}\n");
            fclose(wout);
            wout = fopen(argv[9], "w");
            if (!wout) { perror("s3 out"); return 2; }
            fprintf(wout, "{\"s3_l\":[");
            for (i = 0; i < CBANDS; i++) {
                int k;
                fprintf(wout, "%s[", i ? "," : "");
                for (k = 0; k < CBANDS; k++) fprintf(wout, "%s%.17g", k ? "," : "", probe_f_s3[i][k]);
                fprintf(wout, "]");
            }
            fprintf(wout, "]}\n");
            fclose(wout);
            wout = fopen(argv[11], "w");
            if (!wout) { perror("tables out"); return 2; }
            fprintf(wout, "{");
            print_array_to(wout, "minval", probe_g_minval, CBANDS, 1);
            print_array_to(wout, "qthr_l", probe_g_qthr, CBANDS, 0);
            print_array_to(wout, "norm_l", probe_g_norm, CBANDS, 0);
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
        if (selected(argv[4], call)) {
            print_rows("short_windowed_samples", probe_d_wsamp_s, BLKSIZE_s);
            print_rows("short_energy_prefloor", probe_d_prefloor_s, HBLKSIZE_s);
            print_rows("short_energy", probe_d_energy_s, HBLKSIZE_s);
            print_rows("short_phi", probe_d_phi_s, HBLKSIZE_s);
            printf(",\"long_history\":{\"age_new\":%d,\"age_old\":%d,\"age_oldest\":%d,", probe_d_age_new,
                   probe_d_age_old, probe_d_age_oldest);
            print_six("r_old", probe_d_r_old);
            print_six("r_oldest", probe_d_r_oldest);
            print_six("phi_old", probe_d_phi_old);
            print_six("phi_oldest", probe_d_phi_oldest);
            print_six("r_new", probe_d_r_new);
            print_six("phi_new", probe_d_phi_new);
            printf("}");
            print_array("cw", probe_d_cw, HBLKSIZE);
        }
        if (selected(argv[6], call)) {
            print_floats(stdout, "partition_energy", probe_e_energy, HBLKSIZE);
            print_array("partition_cw", probe_e_cw, HBLKSIZE);
            print_array("eb", probe_e_eb, CBANDS);
            print_floats(stdout, "cb", probe_e_cb, CBANDS);
        }
        if (selected(argv[8], call)) {
            print_floats(stdout, "ecb", probe_f_ecb, CBANDS);
            print_array("ctb", probe_f_ctb, CBANDS);
            printf(",\"ratio_divided\":[");
            for (i = 0; i < CBANDS; i++) printf("%s%d", i ? "," : "", probe_f_divided[i]);
            printf("]");
            print_array("ratio", probe_f_ratio, CBANDS);
            print_array("ratio_clamped", probe_f_ratio_clamped, CBANDS);
            print_array("tonality_input", probe_f_log_ratio, CBANDS);
        }
        if (selected(argv[10], call)) {
            print_floats(stdout, "g_ecb", probe_f_ecb, CBANDS);
            print_array("g_tonality_input", probe_f_log_ratio, CBANDS);
            print_array("tonality_raw", probe_g_tbb_raw, CBANDS);
            print_array("tonality", probe_g_tbb, CBANDS);
            print_array("snr", probe_g_snr, CBANDS);
            print_floats(stdout, "nb", probe_g_nb, CBANDS);
            print_array("nb_1_before", probe_g_nb_1, CBANDS);
            print_array("nb_2_before", probe_g_nb_2, CBANDS);
            print_array("pre_echo_limit", probe_g_temp1, CBANDS);
            print_array("thr", probe_g_thr, CBANDS);
            print_array("nb_1_after", probe_g_nb_1_after, CBANDS);
            print_array("nb_2_after", probe_g_nb_2_after, CBANDS);
        }
        printf("}\n");
    }
    free(clip);
    return 0;
}
