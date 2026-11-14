/*
 * Regression tests for gradient paint parsing.
 *
 * Covers stop offsets (including clamping of out-of-range values), the
 * spreadMethod of linear and radial gradients, the radial focal-point default
 * (fx/fy must default to cx/cy rather than 0), and xlink:href / href
 * inheritance so that nested gradients resolve coordinates, spread, units and
 * stops from their referenced gradient. Malformed references (missing target,
 * cycles) must not crash.
 *
 * Build with a sanitizer for the strongest guarantees:
 *
 *   gcc -std=c99 -g -fsanitize=address,undefined -I ../../src \
 *       gradient_test.c -o gradient_test -lm && ./gradient_test
 */
#define NANOSVG_IMPLEMENTATION
#define NANOSVG_ALL_COLOR_KEYWORDS
#include "nanosvg.h"
#include <stdio.h>
#include <math.h>
#include <string.h>

static int g_failures = 0;

static int feq(float a, float b)
{
	return fabsf(a - b) < 1e-4f;
}

static NSVGgradient* firstGradient(NSVGimage* img, int type, int *shapeIndex)
{
	int i = 0;
	for (NSVGshape* s = img->shapes; s; s = s->next, i++) {
		if (s->fill.type == type) {
			if (shapeIndex) *shapeIndex = i;
			return s->fill.gradient;
		}
	}
	return NULL;
}

static NSVGgradient* gradientAt(NSVGimage* img, int index)
{
	int i = 0;
	for (NSVGshape* s = img->shapes; s; s = s->next, i++) {
		if (i == index)
			return (s->fill.type == NSVG_PAINT_LINEAR_GRADIENT ||
			        s->fill.type == NSVG_PAINT_RADIAL_GRADIENT) ? s->fill.gradient : NULL;
	}
	return NULL;
}

static void check(int cond, const char* name)
{
	if (cond) {
		printf("ok   %s\n", name);
	} else {
		printf("FAIL %s\n", name);
		g_failures++;
	}
}

static NSVGimage* parse(const char* svg)
{
	/* nsvgParse modifies the buffer in place, so hand it a writable copy. */
	size_t n = strlen(svg) + 1;
	char* buf = (char*)malloc(n);
	memcpy(buf, svg, n);
	NSVGimage* img = nsvgParse(buf, "px", 96.0f);
	free(buf);
	return img;
}

static const char* SVG_LINEAR =
	"<svg xmlns='http://www.w3.org/2000/svg' width='200' height='100' viewBox='0 0 200 100'>"
	"<defs><linearGradient id='g' spreadMethod='reflect'>"
	"<stop offset='-20%' stop-color='#ff0000'/>"
	"<stop offset='0.4' stop-color='#00ff00'/>"
	"<stop offset='150%' stop-color='#0000ff'/>"
	"</linearGradient></defs>"
	"<rect width='200' height='100' fill='url(#g)'/></svg>";

static const char* SVG_RADIAL_FOCAL =
	"<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100' viewBox='0 0 100 100'>"
	"<defs><radialGradient id='r' fx='25%' fy='75%' spreadMethod='repeat'>"
	"<stop offset='0.1' stop-color='white'/><stop offset='0.9' stop-color='black'/>"
	"</radialGradient></defs>"
	"<circle cx='50' cy='50' r='40' fill='url(#r)'/></svg>";

static const char* SVG_RADIAL_DEFAULT_FOCAL =
	"<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100' viewBox='0 0 100 100'>"
	"<defs><radialGradient id='r'>"
	"<stop offset='0' stop-color='white'/><stop offset='1' stop-color='black'/>"
	"</radialGradient>"
	"<radialGradient id='re' fx='50%' fy='50%'>"
	"<stop offset='0' stop-color='white'/><stop offset='1' stop-color='black'/>"
	"</radialGradient></defs>"
	"<circle cx='50' cy='50' r='20' fill='url(#r)'/>"
	"<circle cx='50' cy='50' r='20' fill='url(#re)'/></svg>";

static const char* SVG_INHERIT =
	"<svg xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink'"
	" width='100' height='100' viewBox='0 0 100 100'>"
	"<defs><linearGradient id='base' x1='0' y1='0' x2='0' y2='1' spreadMethod='reflect'>"
	"<stop offset='0' stop-color='red'/><stop offset='1' stop-color='blue'/></linearGradient>"
	"<linearGradient id='child' xlink:href='#base'/>"
	"<linearGradient id='override' xlink:href='#base' spreadMethod='repeat'/>"
	"<linearGradient id='svg2' href='#base'/>"
	"</defs>"
	"<rect x='0'  y='0' width='25' height='100' fill='url(#child)'/>"
	"<rect x='25' y='0' width='25' height='100' fill='url(#override)'/>"
	"<rect x='50' y='0' width='25' height='100' fill='url(#svg2)'/>"
	"<rect x='75' y='0' width='25' height='100' fill='url(#base)'/></svg>";

static const char* SVG_DANGLING =
	"<svg xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink'"
	" width='100' height='100' viewBox='0 0 100 100'>"
	"<defs><linearGradient id='k' xlink:href='#missing'>"
	"<stop offset='0' stop-color='red'/><stop offset='1' stop-color='blue'/></linearGradient></defs>"
	"<rect width='100' height='100' fill='url(#k)'/></svg>";

static const char* SVG_CYCLE =
	"<svg xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink'"
	" width='100' height='100' viewBox='0 0 100 100'>"
	"<defs><linearGradient id='k' xlink:href='#k'/></defs>"
	"<rect width='100' height='100' fill='url(#k)'/></svg>";

int main(void)
{
	/* Linear: spread + clamped stop offsets. */
	{
		NSVGimage* img = parse(SVG_LINEAR);
		NSVGgradient* g = img ? firstGradient(img, NSVG_PAINT_LINEAR_GRADIENT, NULL) : NULL;
		check(g != NULL, "linear gradient resolves");
		if (g) {
			check(g->spread == NSVG_SPREAD_REFLECT, "linear spreadMethod=reflect parsed");
			check(g->nstops == 3, "linear keeps all three stops");
			check(feq(g->stops[0].offset, 0.0f), "negative offset clamped to 0");
			check(feq(g->stops[1].offset, 0.4f), "fractional offset preserved");
			check(feq(g->stops[2].offset, 1.0f), "over-1 offset clamped to 1");
		}
		if (img) nsvgDelete(img);
	}

	/* Radial: explicit focal point and repeat spread. */
	{
		NSVGimage* img = parse(SVG_RADIAL_FOCAL);
		NSVGgradient* g = img ? firstGradient(img, NSVG_PAINT_RADIAL_GRADIENT, NULL) : NULL;
		check(g != NULL, "radial gradient resolves");
		if (g) {
			check(g->spread == NSVG_SPREAD_REPEAT, "radial spreadMethod=repeat parsed");
			check(g->nstops == 2, "radial stop count");
			check(feq(g->stops[0].offset, 0.1f) && feq(g->stops[1].offset, 0.9f),
			      "radial fractional offsets preserved");
			/* Normalized focal = focal pixel offset / converted radius. */
			check(feq(g->fx, 0.75f) && feq(g->fy, 1.75f), "explicit focal point normalized");
		}
		if (img) nsvgDelete(img);
	}

	/* Radial: omitted fx/fy must default to the center, matching fx=fy=50%. */
	{
		NSVGimage* img = parse(SVG_RADIAL_DEFAULT_FOCAL);
		NSVGgradient* def = img ? gradientAt(img, 0) : NULL;
		NSVGgradient* explicit = img ? gradientAt(img, 1) : NULL;
		check(def != NULL && explicit != NULL, "default-focal gradients resolve");
		if (def && explicit) {
			check(feq(def->fx, explicit->fx) && feq(def->fy, explicit->fy),
			      "omitted focal defaults to center (fx=cx, fy=cy)");
			/*
			 * Center at 50% against this shape's bounds normalizes to 2.5
			 * (focal pixel offset 50 divided by the converted radius 20).
			 */
			check(feq(def->fx, 2.5f) && feq(def->fy, 2.5f),
			      "default focal is the circle center in normalized units");
		}
		if (img) nsvgDelete(img);
	}

	/* Inheritance: child/override/svg2 vs the directly-defined base. */
	{
		NSVGimage* img = parse(SVG_INHERIT);
		NSVGgradient* child = img ? gradientAt(img, 0) : NULL;
		NSVGgradient* override = img ? gradientAt(img, 1) : NULL;
		NSVGgradient* svg2 = img ? gradientAt(img, 2) : NULL;
		NSVGgradient* base = img ? gradientAt(img, 3) : NULL;
		check(child && override && svg2 && base, "all inherited gradients resolve");
		if (child && base) {
			check(child->nstops == base->nstops, "child inherits stop count");
			check(child->spread == NSVG_SPREAD_REFLECT, "child inherits spreadMethod");
			check(memcmp(child->xform, base->xform, sizeof(child->xform)) == 0,
			      "child inherits gradient geometry/xform");
		}
		if (override)
			check(override->spread == NSVG_SPREAD_REPEAT, "child spreadMethod override wins");
		if (svg2 && base) {
			check(svg2->nstops == base->nstops, "SVG2 href (no xlink) inherits stops");
			check(svg2->spread == NSVG_SPREAD_REFLECT, "SVG2 href inherits spreadMethod");
		}
		if (img) nsvgDelete(img);
	}

	/* Robustness: dangling reference with local stops still renders. */
	{
		NSVGimage* img = parse(SVG_DANGLING);
		NSVGgradient* g = img ? firstGradient(img, NSVG_PAINT_LINEAR_GRADIENT, NULL) : NULL;
		check(g != NULL && g->nstops == 2, "dangling href falls back to local stops");
		if (img) nsvgDelete(img);
	}

	/* Robustness: self-referential cycle terminates without crashing. */
	{
		NSVGimage* img = parse(SVG_CYCLE);
		check(img != NULL, "self-referential href does not crash");
		if (img) nsvgDelete(img);
	}

	if (g_failures != 0) {
		printf("\n%d check(s) failed\n", g_failures);
		return 1;
	}
	printf("\nall checks passed\n");
	return 0;
}
