// Micro-benchmark for the path lexer + number conversion only.
// It feeds the raw 'd' attribute text through the exact same scanning path
// nsvg__parsePath uses, without geometry construction or XML.
#define NANOSVG_IMPLEMENTATION
#include "nanosvg.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double now_sec(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}

static char* read_file(const char* path, size_t* len_out)
{
	FILE* f = fopen(path, "rb");
	if (!f) { perror(path); return NULL; }
	fseek(f, 0, SEEK_END);
	long len = ftell(f);
	fseek(f, 0, SEEK_SET);
	char* buf = (char*)malloc((size_t)len + 1);
	if (fread(buf, 1, (size_t)len, f) != (size_t)len) { /* checked below */ }
	buf[len] = '\0';
	fclose(f);
	if (len_out) *len_out = (size_t)len;
	return buf;
}

// Extract the d="..." body so both builds scan identical input.
static char* extract_d(const char* svg, size_t* dlen)
{
	const char* p = strstr(svg, "d=\"");
	if (!p) return NULL;
	p += 3;
	const char* q = strchr(p, '"');
	size_t n = (size_t)(q - p);
	char* out = (char*)malloc(n + 1);
	memcpy(out, p, n);
	out[n] = '\0';
	if (dlen) *dlen = n;
	return out;
}

#ifdef OPT_LEXER
static double run_opt(const char* d, unsigned long* sink)
{
	double t0 = now_sec();
	const char* s = d;
	NSVGpathToken tok;
	double sum = 0;
	long cmds = 0;
	while (*s) {
		s = nsvg__nextPathToken(s, &tok, 0);
		if (!tok.isNumber && tok.command == '\0') break;
		if (tok.isNumber) sum += tok.value;
		else cmds++;
	}
	double t1 = now_sec();
	*sink = (unsigned long)cmds ^ (unsigned long long)(sum * 1.5);
	return t1 - t0;
}
#else
static double run_ref(const char* d, unsigned long* sink)
{
	double t0 = now_sec();
	const char* s = d;
	char item[64];
	double sum = 0;
	long cmds = 0;
	while (*s) {
		item[0] = '\0';
		s = nsvg__getNextPathItem(s, item);
		if (!*item) break;
		if (nsvg__isCoordinate(item)) sum += nsvg__atof(item);
		else cmds++;
	}
	double t1 = now_sec();
	*sink = (unsigned long)cmds ^ (unsigned long long)(sum * 1.5);
	return t1 - t0;
}
#endif

int main(int argc, char** argv)
{
	if (argc < 3) {
		fprintf(stderr, "usage: %s <file.svg> <iters>\n", argv[0]);
		return 2;
	}
	size_t len;
	char* svg = read_file(argv[1], &len);
	size_t dlen;
	char* d = extract_d(svg, &dlen);
	int iters = atoi(argv[2]);
	double best = 1e30;
	unsigned long sink = 0;
	for (int i = 0; i < iters; ++i) {
#ifdef OPT_LEXER
		double dt = run_opt(d, &sink);
#else
		double dt = run_ref(d, &sink);
#endif
		if (dt < best) best = dt;
	}
	printf("dbytes=%zu iters=%d best=%.4f sec %.1f MB/s sink=%lu\n",
		dlen, iters, best, (dlen / (1024.0 * 1024.0)) / best, sink);
	free(d);
	free(svg);
	return 0;
}
