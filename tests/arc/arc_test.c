/*
 * Regression test for malformed SVG arc (A/a) radii.
 *
 * Verifies that zero, negative, subnormal and float-overflowing radii never
 * produce non-finite geometry or invalid path topology, and that arcs which
 * must degrade to a straight line still commit a path (no silently dropped
 * shapes). Build with a sanitizer for the strongest guarantees:
 *
 *   gcc -std=c99 -g -fsanitize=address,undefined -I ../../src \
 *       arc_test.c -o arc_test -lm && ./arc_test
 */
#define NANOSVG_IMPLEMENTATION
#include "nanosvg.h"
#include <stdio.h>
#include <math.h>

static int g_failures = 0;

static void check(const char* name, const char* d, int expectPath)
{
	char svg[2048];
	int paths = 0, nonfinite = 0, badTopo = 0;

	snprintf(svg, sizeof(svg),
		"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"200\" height=\"200\""
		" viewBox=\"0 0 200 200\"><path d=\"%s\"/></svg>", d);

	NSVGimage* img = nsvgParse(svg, "px", 96);
	if (img != NULL) {
		for (NSVGshape* sh = img->shapes; sh; sh = sh->next) {
			for (NSVGpath* p = sh->paths; p; p = p->next) {
				paths++;
				if ((p->npts % 3) != 1) badTopo++;
				for (int i = 0; i < p->npts; i++) {
					if (!isfinite(p->pts[i*2]) || !isfinite(p->pts[i*2+1]))
						nonfinite++;
				}
			}
		}
		nsvgDelete(img);
	}

	if (nonfinite != 0 || badTopo != 0 || (expectPath && paths == 0)) {
		printf("FAIL %-22s paths=%d nonfinite=%d badTopo=%d\n",
			name, paths, nonfinite, badTopo);
		g_failures++;
	} else {
		printf("ok   %-22s paths=%d\n", name, paths);
	}
}

int main(void)
{
	check("normal arc",        "M10,10 A20,15 0 0 1 60,60", 1);
	check("zero rx",           "M10,10 A0,15 0 0 1 60,60", 1);
	check("zero ry",           "M10,10 A20,0 0 0 1 60,60", 1);
	check("zero both",         "M10,10 A0,0 0 0 1 60,60", 1);
	check("negative rx",       "M10,10 A-20,15 0 0 1 60,60", 1);
	check("negative ry",       "M10,10 A20,-15 0 0 1 60,60", 1);
	check("negative both",     "M10,10 A-20,-15 0 0 1 60,60", 1);
	check("subnormal radius",  "M10,10 A1e-400,15 0 0 1 60,60", 1);
	check("overflow rx 1e99",  "M10,10 A1e99,15 0 0 1 60,60", 1);
	check("overflow rx 1e30",  "M10,10 A1e30,15 0 0 1 60,60", 1);
	check("overflow both",     "M10,10 A-1e99,-1e99 0 1 1 60,60", 1);
	check("long-digit radius", "M10,10 A9999999999999999999999999999999999999999,15 0 0 1 60,60", 1);
	check("embedded overflow", "M10,10 L20,20 A1e30,15 0 0 1 80,80 L90,90", 1);
	check("coincident ends",   "M50,50 A20,15 0 0 1 50,50", 1);

	if (g_failures != 0) {
		printf("\n%d check(s) failed\n", g_failures);
		return 1;
	}
	printf("\nall checks passed\n");
	return 0;
}
