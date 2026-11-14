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
	if (fread(buf, 1, (size_t)len, f) != (size_t)len) { fprintf(stderr, "short read\n"); return NULL; }
	buf[len] = '\0';
	fclose(f);
	if (len_out) *len_out = (size_t)len;
	return buf;
}

static void dump_image(NSVGimage* img)
{
	printf("IMG %.9g %.9g\n", img->width, img->height);
	int si = 0;
	for (NSVGshape* sh = img->shapes; sh; sh = sh->next, ++si) {
		printf("SH %d id='%s' fill.type=%d fill.color=0x%08x stroke.type=%d "
		       "stroke.color=0x%08x opacity=%.9g strokeWidth=%.9g "
		       "dashOff=%.9g dashCnt=%d join=%d cap=%d miter=%.9g "
		       "fillRule=%d flags=%u bounds=%.9g %.9g %.9g %.9g\n",
			si, sh->id, sh->fill.type, sh->fill.color,
			sh->stroke.type, sh->stroke.color, sh->opacity, sh->strokeWidth,
			sh->strokeDashOffset, (int)sh->strokeDashCount,
			(int)sh->strokeLineJoin, (int)sh->strokeLineCap, sh->miterLimit,
			(int)sh->fillRule, (unsigned)sh->flags,
			sh->bounds[0], sh->bounds[1], sh->bounds[2], sh->bounds[3]);
		int pi = 0;
		for (NSVGpath* pa = sh->paths; pa; pa = pa->next, ++pi) {
			printf("PA %d %d closed=%d bounds=%.9g %.9g %.9g %.9g\n",
				pi, pa->npts, (int)pa->closed,
				pa->bounds[0], pa->bounds[1], pa->bounds[2], pa->bounds[3]);
			for (int k = 0; k < pa->npts; ++k)
				printf("PT %d %.9g %.9g\n", k, pa->pts[2 * k], pa->pts[2 * k + 1]);
		}
	}
}

int main(int argc, char** argv)
{
	if (argc < 3) {
		fprintf(stderr, "usage: %s dump|bench <file.svg> [iters]\n", argv[0]);
		return 2;
	}
	int dump = strcmp(argv[1], "dump") == 0;
	size_t len;
	char* data = read_file(argv[2], &len);
	if (!data) return 1;
	int iters = dump ? 1 : (argc > 3 ? atoi(argv[3]) : 20);

	NSVGimage* img = NULL;
	double best = 1e30;
	for (int it = 0; it < iters; ++it) {
		char* copy = (char*)malloc(len + 1);
		memcpy(copy, data, len + 1);
		double t0 = now_sec();
		img = nsvgParse(copy, "px", 96.0f);
		double t1 = now_sec();
		if (t1 - t0 < best) best = t1 - t0;
		if (it < iters - 1) {
			nsvgDelete(img);
			free(copy);
		} else {
			free(copy);
		}
	}

	if (dump) {
		dump_image(img);
	} else {
		long shapes = 0, paths = 0, pts = 0;
		for (NSVGshape* sh = img->shapes; sh; sh = sh->next) {
			++shapes;
			for (NSVGpath* pa = sh->paths; pa; pa = pa->next) {
				++paths;
				pts += pa->npts;
			}
		}
		printf("file=%s bytes=%zu iters=%d best=%.4f sec %.1f MB/s "
		       "shapes=%ld paths=%ld pts=%ld\n",
			argv[2], len, iters, best, (len / (1024.0 * 1024.0)) / best,
			shapes, paths, pts);
	}
	nsvgDelete(img);
	free(data);
	return 0;
}
