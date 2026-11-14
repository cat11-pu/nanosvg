// Token-level equivalence check between the reference string lexer+atof and
// the optimized direct-scan lexer. Compares each numeric token as the exact
// float that parsePath would store, plus the command stream.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NANOSVG_IMPLEMENTATION
#include "nanosvg.h"

#ifdef OPT_BUILD
#define HDR "opt"
#else
#define HDR "ref"
#endif

static char* read_file(const char* path)
{
	FILE* f = fopen(path, "rb");
	if (!f) { perror(path); exit(1); }
	fseek(f, 0, SEEK_END);
	long len = ftell(f);
	fseek(f, 0, SEEK_SET);
	char* buf = (char*)malloc((size_t)len + 1);
	if (fread(buf, 1, (size_t)len, f) != (size_t)len) { /* io */ }
	buf[len] = '\0';
	fclose(f);
	return buf;
}

int main(int argc, char** argv)
{
	if (argc < 2) return 2;
	char* svg = read_file(argv[1]);
	char* d0 = strstr(svg, "d=\"");
	d0 += 3;
	char* end = strchr(d0, '"');
	*end = '\0';
	const char* s = d0;

#ifdef OPT_BUILD
	NSVGpathToken tok;
	while (*s) {
		s = nsvg__nextPathToken(s, &tok, 0);
		if (!tok.isNumber && tok.command == '\0') break;
		if (tok.isNumber)
			printf("N %.9g\n", tok.value);
		else
			printf("C %c\n", tok.command);
	}
#else
	char item[64];
	while (*s) {
		item[0] = '\0';
		s = nsvg__getNextPathItem(s, item);
		if (!*item) break;
		if (nsvg__isCoordinate(item))
			printf("N %.9g\n", (float)nsvg__atof(item));
		else
			printf("C %c\n", item[0]);
	}
#endif
	free(svg);
	return 0;
}
