/* Host test: lay out a page produced by `html2ce.py --bin` and print the wrapped lines.
 *   gcc -Wall -Wextra -I../src test_core.c ../src/core.c -o test_core
 *   ./test_core PAGE.bin [cols]
 */
#include <stdio.h>
#include <stdlib.h>

#include "core.h"

int main(int argc, char **argv)
{
    static uint8_t buf[70000];
    Doc d;
    FILE *f;
    size_t n;
    unsigned cols = argc > 2 ? (unsigned)atoi(argv[2]) : 39;
    int r, bad = 0;
    uint16_t i;

    if (argc < 2 || !(f = fopen(argv[1], "rb"))) {
        fprintf(stderr, "usage: %s PAGE.bin [cols]\n", argv[0]);
        return 2;
    }
    n = fread(buf, 1, sizeof buf, f);
    fclose(f);

    r = doc_layout(&d, buf, (uint16_t)n, (uint8_t)cols);
    if (r != DOC_OK) {
        printf("doc_layout failed: %d\n", r);
        return 1;
    }
    printf("title=\"%s\" prev=\"%s\" next=\"%s\" lines=%u truncated=%d\n",
           d.title, d.prev, d.next, d.nlines, d.truncated);
    for (i = 0; i < d.nlines; i++) {
        uint8_t len;
        const char *s = doc_line(&d, i, &len);

        printf("%3u |%.*s%*s|\n", i, len, s, (int)(cols - len), "");
        if (len > cols)
            bad++;
    }
    if (bad)
        printf("ERROR: %d lines wider than %u columns\n", bad, cols);
    return bad != 0;
}
