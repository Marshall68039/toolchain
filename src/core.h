/*
 * core.h - page loading and word-wrapping for the HTML reader.
 *
 * Pure C with no calculator dependencies, so it can be tested on a PC.
 * Reads the page format produced by html2ce.py (format version 1).
 */
#ifndef CORE_H
#define CORE_H

#include <stdbool.h>
#include <stdint.h>

#define ARENA_CAP 48000u /* bytes of wrapped text kept in RAM per page */
#define LINE_CAP  5000u  /* max wrapped lines per page                 */

enum {
    DOC_OK = 0,
    DOC_BAD_HEADER, /* too short or inconsistent header */
    DOC_BAD_MAGIC,  /* not an html2ce page              */
    DOC_BAD_VERSION /* format version we don't know     */
};

typedef struct {
    char     title[41];
    char     prev[9];     /* AppVar name of previous page, "" if none */
    char     next[9];     /* AppVar name of next page, "" if none     */
    uint16_t nlines;      /* number of wrapped lines                  */
    bool     truncated;   /* page didn't fit the buffers              */
} Doc;

/* Parse and word-wrap a page to `cols` columns. `data` points at the AppVar
 * contents (after the 2-byte size). Replaces any previously laid-out page. */
int doc_layout(Doc *d, const uint8_t *data, uint16_t size, uint8_t cols);

/* Wrapped line i. The text is NOT NUL-terminated; its length goes in *len. */
const char *doc_line(const Doc *d, uint16_t i, uint8_t *len);

#endif
