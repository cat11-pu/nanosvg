#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef MALLOC_AUDIT
#define AUDIT_MAX_ALLOCS (1u << 20)
static void* audit_ptrs[AUDIT_MAX_ALLOCS];
static size_t audit_sizes[AUDIT_MAX_ALLOCS];
static volatile size_t audit_count;
static volatile size_t audit_bytes;
static volatile int audit_active;
#ifdef MALLOC_AUDIT_SELFTEST
static void* volatile audit_selftest_ptr;
#endif

void* __real_malloc(size_t size);
void* __real_calloc(size_t count, size_t size);
void* __real_realloc(void* ptr, size_t size);
void __real_free(void* ptr);

static void audit_add(void* ptr, size_t size)
{
	if (!audit_active || ptr == NULL || size == 0)
		return;
	if (audit_count >= AUDIT_MAX_ALLOCS) {
		fprintf(stderr, "MALLOC_AUDIT overflow\n");
		abort();
	}
	audit_ptrs[audit_count] = ptr;
	audit_sizes[audit_count] = size;
	audit_count++;
	audit_bytes += size;
}

static int audit_remove(void* ptr)
{
	size_t i;
	if (!audit_active || ptr == NULL)
		return 0;
	for (i = 0; i < audit_count; ++i) {
		if (audit_ptrs[i] == ptr) {
			audit_bytes -= audit_sizes[i];
			audit_count--;
			audit_ptrs[i] = audit_ptrs[audit_count];
			audit_sizes[i] = audit_sizes[audit_count];
			return 1;
		}
	}
	return 0;
}

void* __wrap_malloc(size_t size)
{
	void* ptr = __real_malloc(size);
	audit_add(ptr, size);
	return ptr;
}

void* __wrap_calloc(size_t count, size_t size)
{
	void* ptr = __real_calloc(count, size);
	audit_add(ptr, count * size);
	return ptr;
}

void* __wrap_realloc(void* ptr, size_t size)
{
	void* result;
	if (!audit_active)
		return __real_realloc(ptr, size);
	result = __real_realloc(ptr, size);
	if (size == 0) {
		audit_remove(ptr);
		return result;
	}
	if (result != NULL) {
		audit_remove(ptr);
		audit_add(result, size);
	}
	return result;
}

void __wrap_free(void* ptr)
{
	audit_remove(ptr);
	__real_free(ptr);
}

static void audit_start(void)
{
	audit_count = 0;
	audit_bytes = 0;
	audit_active = 1;
}

static void audit_stop(void)
{
	audit_active = 0;
}

static int audit_report(void)
{
	if (audit_count == 0) {
		printf("LEAK_AUDIT clean\n");
		return 0;
	}
	printf("LEAK_AUDIT leaks=%zu bytes=%zu\n", audit_count, audit_bytes);
	if (audit_count > 0)
		fprintf(stderr, "first leak: ptr=%p size=%zu\n",
		        audit_ptrs[0], audit_sizes[0]);
	return 1;
}
#endif

#define NANOSVG_IMPLEMENTATION
#include "../src/nanosvg.h"

static unsigned char* read_file(const char* path, size_t* size_out)
{
	FILE* fp;
	long size;
	unsigned char* data;

	fp = fopen(path, "rb");
	if (fp == NULL) {
		fprintf(stderr, "open failed: %s\n", strerror(errno));
		return NULL;
	}
	if (fseek(fp, 0, SEEK_END) != 0) {
		fclose(fp);
		return NULL;
	}
	size = ftell(fp);
	if (size < 0) {
		fclose(fp);
		return NULL;
	}
	if (fseek(fp, 0, SEEK_SET) != 0) {
		fclose(fp);
		return NULL;
	}
	data = (unsigned char*)malloc((size_t)size + 1);
	if (data == NULL) {
		fclose(fp);
		return NULL;
	}
	if (size > 0 && fread(data, 1, (size_t)size, fp) != (size_t)size) {
		free(data);
		fclose(fp);
		return NULL;
	}
	fclose(fp);
	data[size] = '\0';
	*size_out = (size_t)size;
	return data;
}

static void print_float_bits(float value)
{
	uint32_t bits;
	memcpy(&bits, &value, sizeof(bits));
	printf("%08" PRIx32, bits);
}

static void dump_image(const NSVGimage* image)
{
	const NSVGshape* shape;
	const NSVGpath* path;
	int shape_index = 0;
	int path_index;
	int i;

	printf("IMAGE ");
	print_float_bits(image->width);
	printf(" ");
	print_float_bits(image->height);
	printf("\n");

	for (shape = image->shapes; shape != NULL; shape = shape->next) {
		printf("SHAPE %d %d %d ", shape_index++, (int)shape->fill.type, (int)shape->stroke.type);
		print_float_bits(shape->opacity);
		printf(" ");
		print_float_bits(shape->strokeWidth);
		printf("\n");
		path_index = 0;
		for (path = shape->paths; path != NULL; path = path->next) {
			printf("PATH %d %d", path_index++, (int)path->closed);
			printf(" %d", path->npts);
			printf(" ");
			print_float_bits(path->bounds[0]);
			printf(" ");
			print_float_bits(path->bounds[1]);
			printf(" ");
			print_float_bits(path->bounds[2]);
			printf(" ");
			print_float_bits(path->bounds[3]);
			printf(" ");
			for (i = 0; i < path->npts * 2; ++i) {
				if (i)
					printf(" ");
				print_float_bits(path->pts[i]);
			}
			printf("\n");
		}
	}
}

int main(int argc, char** argv)
{
	unsigned char* file_data;
	char* input;
	size_t size;
	NSVGimage* image;
	int dump = 0;
#ifdef MALLOC_AUDIT
	int leak_failure;
#endif

#ifdef MALLOC_AUDIT_SELFTEST
	audit_start();
	audit_selftest_ptr = malloc(1234);
	audit_stop();
	return audit_report() ? 0 : 1;
#endif

	if (argc < 2 || argc > 3) {
		fprintf(stderr, "usage: %s FILE [--dump]\n", argv[0]);
		return 2;
	}
	if (argc == 3 && strcmp(argv[2], "--dump") == 0)
		dump = 1;
	else if (argc == 3)
		return 2;

	file_data = read_file(argv[1], &size);
	if (file_data == NULL)
		return 2;

	/* nsvgParse mutates its input, so give it a private, mutable copy. */
	input = (char*)malloc(size + 1);
	if (input == NULL) {
		free(file_data);
		return 2;
	}
	memcpy(input, file_data, size + 1);
	free(file_data);

#ifdef MALLOC_AUDIT
	audit_start();
#endif
	image = nsvgParse(input, "px", 96.0f);
#ifdef MALLOC_AUDIT
	nsvgDelete(image);
	audit_stop();
	leak_failure = audit_report();
	free(input);
	if (leak_failure) {
		return 4;
	}
	if (image == NULL) {
		printf("PARSE_RESULT null\n");
		return 3;
	}
	printf("PARSE_RESULT image\n");
	return 0;
#else
	free(input);

	if (image == NULL) {
		printf("PARSE_RESULT null\n");
		nsvgDelete(image);
		return 3;
	}

	printf("PARSE_RESULT image\n");
	if (dump)
		dump_image(image);
	nsvgDelete(image);
	return 0;
#endif
}
