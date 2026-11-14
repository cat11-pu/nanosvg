#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#define NANOSVG_IMPLEMENTATION
#include "../src/nanosvg.h"
#define NANOSVGRAST_IMPLEMENTATION
#include "../src/nanosvgrast.h"

int main(int argc, char** argv)
{
	NSVGimage* image;
	NSVGrasterizer* rasterizer;
	unsigned char* pixels;
	int width;
	int height;
	int stride;
	int x;
	int y;

	if (argc != 2) {
		fprintf(stderr, "usage: %s FILE\n", argv[0]);
		return 2;
	}
	image = nsvgParseFromFile(argv[1], "px", 96.0f);
	if (image == NULL)
		return 2;
	width = (int)image->width;
	height = (int)image->height;
	if (width <= 0 || height <= 0 || width > 4096 || height > 4096) {
		nsvgDelete(image);
		return 2;
	}
	stride = width * 4;
	pixels = (unsigned char*)malloc((size_t)stride * (size_t)height);
	if (pixels == NULL) {
		nsvgDelete(image);
		return 2;
	}
	rasterizer = nsvgCreateRasterizer();
	if (rasterizer == NULL) {
		free(pixels);
		nsvgDelete(image);
		return 2;
	}
	nsvgRasterize(rasterizer, image, 0.0f, 0.0f, 1.0f,
	              pixels, width, height, stride);

	printf("P5\n%d %d\n255\n", width, height);
	for (y = 0; y < height; ++y) {
		for (x = 0; x < width; ++x)
			fputc(pixels[y * stride + x * 4 + 3], stdout);
	}

	nsvgDeleteRasterizer(rasterizer);
	free(pixels);
	nsvgDelete(image);
	return 0;
}
