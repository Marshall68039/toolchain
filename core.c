#include "core.h"

#include <string.h>

/* Token opcodes - must match html2ce.py */
enum {
    T_END, T_HEAD, T_PARA, T_BR, T_CENTER, T_LINK, T_LINK_END,
    T_BOLD, T_ITALIC, T_IMAGE, T_INDENT, T_PRE, T_RULE
};

#define MAX_COLS 60

/* Wrapped lines live here as [length byte][chars...], indexed by lines[]. */
static char     arena[ARENA_CAP];
static uint16_t lines[LINE_CAP];

typedef struct {
    Doc     *doc;
    uint8_t  cols;
    uint16_t arena_len;
    uint16_t nlines;
    char     cur[MAX_COLS + 1]; /* line being built (starts with indent spaces) */
    uint8_t  clen;
    bool     has_text;          /* cur holds more than just indent              */
    char     word[MAX_COLS + 1];
    uint8_t  wlen;
    uint8_t  ind;               /* indent in columns                            */
    bool     pre;
    bool     full;
} Layout;

/* ---- line output -------------------------------------------------------------- */

static void commit(Layout *l)
{
    uint8_t n = l->clen;

    while (n && l->cur[n - 1] == ' ')
        n--;
    if (l->full)
        return;
    if (l->nlines >= LINE_CAP || (uint32_t)l->arena_len + 1u + n > ARENA_CAP) {
        l->full = true;
        l->doc->truncated = true;
        return;
    }
    lines[l->nlines++] = l->arena_len;
    arena[l->arena_len++] = (char)n;
    memcpy(arena + l->arena_len, l->cur, n);
    l->arena_len += n;
}

static void start_line(Layout *l)
{
    l->clen = l->ind;
    memset(l->cur, ' ', l->ind);
    l->has_text = false;
}

static void newline(Layout *l)
{
    commit(l);
    start_line(l);
}

static bool last_line_blank(const Layout *l)
{
    return l->nlines > 0 && arena[lines[l->nlines - 1]] == 0;
}

/* ---- words -------------------------------------------------------------------- */

static void flush_word(Layout *l)
{
    if (!l->wlen)
        return;
    if (l->has_text && (unsigned)l->clen + 1u + l->wlen > l->cols)
        newline(l);
    if (l->has_text)
        l->cur[l->clen++] = ' ';
    memcpy(l->cur + l->clen, l->word, l->wlen);
    l->clen += l->wlen;
    l->has_text = true;
    l->wlen = 0;
}

static void add_char(Layout *l, char c)
{
    if (l->pre) { /* keep every space, wrap hard at the screen edge */
        if (l->clen >= l->cols)
            newline(l);
        l->cur[l->clen++] = c;
        l->has_text = true;
        return;
    }
    if (c == ' ') {
        flush_word(l);
        return;
    }
    if (l->wlen >= l->cols - l->ind) { /* single word wider than a line: split it */
        flush_word(l);
        newline(l);
    }
    l->word[l->wlen++] = c;
}

static void add_str(Layout *l, const char *s)
{
    while (*s)
        add_char(l, *s++);
}

/* ---- breaks ------------------------------------------------------------------- */

static void end_line(Layout *l) /* <br> */
{
    flush_word(l);
    if (l->nlines == 0 && !l->has_text) { /* ignore leading blank lines */
        start_line(l);
        return;
    }
    newline(l);
}

static void end_para(Layout *l) /* paragraph: finish the line, then a blank one */
{
    flush_word(l);
    if (l->has_text)
        newline(l);
    if (l->nlines > 0 && !last_line_blank(l)) {
        uint8_t saved = l->clen;
        l->clen = 0;
        commit(l);
        l->clen = saved;
    }
}

static void rule(Layout *l)
{
    flush_word(l);
    if (l->has_text)
        newline(l);
    memset(l->cur, '-', l->cols);
    l->clen = l->cols;
    commit(l);
    start_line(l);
}

/* ---- public API --------------------------------------------------------------- */

static void copy_name(char *dst, const uint8_t *src)
{
    uint8_t i;

    for (i = 0; i < 8 && src[i]; i++)
        dst[i] = (char)src[i];
    dst[i] = 0;
}

int doc_layout(Doc *d, const uint8_t *data, uint16_t size, uint8_t cols)
{
    Layout   l;
    uint32_t pos;
    uint8_t  tl, c, n, i;

    memset(d, 0, sizeof *d);
    if (size < 21)
        return DOC_BAD_HEADER;
    if (data[0] != 'H' || data[1] != 'R')
        return DOC_BAD_MAGIC;
    if (data[2] != 1)
        return DOC_BAD_VERSION;
    tl = data[20];
    if (tl > 40 || 21u + tl > size)
        return DOC_BAD_HEADER;
    copy_name(d->prev, data + 4);
    copy_name(d->next, data + 12);
    memcpy(d->title, data + 21, tl);
    d->title[tl] = 0;
    pos = 21u + tl;

    memset(&l, 0, sizeof l);
    l.doc = d;
    l.cols = cols > MAX_COLS ? MAX_COLS : (cols < 16 ? 16 : cols);
    start_line(&l);

#define ARG() (pos < size ? data[pos++] : 0)

    while (pos < size && !l.full) {
        c = data[pos++];
        if (c >= 0x20) {
            add_char(&l, (char)c);
            continue;
        }
        switch (c) {
        case T_END:
            goto done;
        case T_HEAD: case T_CENTER: case T_BOLD: case T_ITALIC:
            (void)ARG(); /* styling is ignored in the plain view */
            break;
        case T_PARA:
            end_para(&l);
            break;
        case T_BR:
            end_line(&l);
            break;
        case T_LINK:
            pos += 8;
            break;
        case T_LINK_END:
            break;
        case T_IMAGE:
            n = ARG();
            add_str(&l, n ? "[image: " : "[image]");
            for (i = 0; i < n && pos < size; i++)
                add_char(&l, (char)data[pos++]);
            if (n)
                add_str(&l, "]");
            break;
        case T_INDENT:
            n = ARG();
            flush_word(&l);
            l.ind = (uint8_t)(2 * (n > 3 ? 3 : n));
            if (!l.has_text)
                start_line(&l); /* re-indent the empty line we're on */
            break;
        case T_PRE:
            flush_word(&l);
            l.pre = ARG() != 0;
            break;
        case T_RULE:
            rule(&l);
            break;
        default:
            break;
        }
    }
done:
    flush_word(&l);
    if (l.has_text)
        commit(&l);
    d->nlines = l.nlines;
    return DOC_OK;
}

const char *doc_line(const Doc *d, uint16_t i, uint8_t *len)
{
    const char *p;

    if (i >= d->nlines) {
        *len = 0;
        return "";
    }
    p = arena + lines[i];
    *len = (uint8_t)p[0];
    return p + 1;
}
