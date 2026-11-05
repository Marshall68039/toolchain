/*
 * HTMLRD - plain scrolling reader for pages converted with html2ce.py
 *
 * Menu:   Up/Down choose a page, Enter or 2nd opens it, Clear quits.
 * Reader: Up/Down scroll a line (hold to repeat), 2nd / Alpha page down / up,
 *         Right / Left next / previous page of a long document,
 *         Clear back to the menu.
 */

#include <fileioc.h>
#include <graphx.h>
#include <keypadc.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <tice.h>

#include "core.h"

#define COLS     39   /* 39 * 8px + 2 * 4px margin = 320px */
#define MARGIN_X 4
#define BAR_H    12
#define LINE_H   9
#define TOP_Y    (BAR_H + 3)
#define VISIBLE  ((240 - BAR_H - 3 - BAR_H) / LINE_H)

#define LIST_H    10
#define LIST_ROWS ((240 - 2 * BAR_H - 4) / LIST_H)
#define MAX_FILES 48

#define C_BLACK 0   /* default graphx palette */
#define C_WHITE 255

#define REPEAT_DELAY 12 /* frames before a held Up/Down starts repeating */
#define REPEAT_STEP  2

enum { K_UP = 1, K_DOWN = 2, K_LEFT = 4, K_RIGHT = 8,
       K_ENTER = 16, K_CLEAR = 32, K_2ND = 64, K_ALPHA = 128 };

typedef struct {
    char name[9];
    char title[41];
} Entry;

static Entry entries[MAX_FILES];
static Doc   doc;
static char  cur_name[9];

/* ---- input -------------------------------------------------------------------- */

static uint8_t poll_keys(void)
{
    uint8_t k = 0;

    kb_Scan();
    if (kb_Data[7] & kb_Up)    k |= K_UP;
    if (kb_Data[7] & kb_Down)  k |= K_DOWN;
    if (kb_Data[7] & kb_Left)  k |= K_LEFT;
    if (kb_Data[7] & kb_Right) k |= K_RIGHT;
    if (kb_Data[6] & kb_Enter) k |= K_ENTER;
    if (kb_Data[6] & kb_Clear) k |= K_CLEAR;
    if (kb_Data[1] & kb_2nd)   k |= K_2ND;
    if (kb_Data[2] & kb_Alpha) k |= K_ALPHA;
    return k;
}

/* Keys that went down since last call, plus auto-repeat for Up/Down. */
static uint8_t key_events(void)
{
    static uint8_t  prev;
    static uint16_t hold;
    uint8_t now = poll_keys();
    uint8_t ev = now & (uint8_t)~prev;

    if ((now & (K_UP | K_DOWN)) && now == prev) {
        if (hold < 60000u)
            hold++;
        if (hold >= REPEAT_DELAY && (hold - REPEAT_DELAY) % REPEAT_STEP == 0)
            ev |= now & (K_UP | K_DOWN);
    } else {
        hold = 0;
    }
    prev = now;
    return ev;
}

/* ---- files -------------------------------------------------------------------- */

/* Find every converted page that is the first page of its document. */
static uint8_t scan_pages(void)
{
    void   *pos = NULL;
    char   *name;
    uint8_t n = 0, h, i;

    while (n < MAX_FILES && (name = ti_Detect(&pos, "HR")) != NULL) {
        const uint8_t *p;
        uint16_t       size;

        h = ti_Open(name, "r");
        if (!h)
            continue;
        p = (const uint8_t *)ti_GetDataPtr(h);
        size = ti_GetSize(h);
        if (size >= 21 && p[2] == 1 && p[20] <= 40) {
            bool first = true;

            for (i = 4; i < 12; i++) /* skip continuation pages (they have a prev) */
                if (p[i])
                    first = false;
            if (first) {
                strncpy(entries[n].name, name, 8);
                entries[n].name[8] = 0;
                memcpy(entries[n].title, p + 21, p[20]);
                entries[n].title[p[20]] = 0;
                n++;
            }
        }
        ti_Close(h);
    }
    return n;
}

static bool load_page(const char *name)
{
    uint8_t h = ti_Open(name, "r");
    int     r;

    if (!h)
        return false;
    r = doc_layout(&doc, (const uint8_t *)ti_GetDataPtr(h), ti_GetSize(h), COLS);
    ti_Close(h);
    if (r != DOC_OK)
        return false;
    strncpy(cur_name, name, 8);
    cur_name[8] = 0;
    return true;
}

/* ---- drawing ------------------------------------------------------------------ */

static void text_on_white(void)
{
    gfx_SetTextFGColor(C_BLACK);
    gfx_SetTextBGColor(C_WHITE);
    gfx_SetTextTransparentColor(C_WHITE);
}

static void text_on_black(void)
{
    gfx_SetTextFGColor(C_WHITE);
    gfx_SetTextBGColor(C_BLACK);
    gfx_SetTextTransparentColor(C_BLACK);
}

static void draw_bars(const char *top, const char *bottom)
{
    gfx_FillScreen(C_WHITE);
    gfx_SetColor(C_BLACK);
    gfx_FillRectangle(0, 0, 320, BAR_H);
    gfx_FillRectangle(0, 240 - BAR_H, 320, BAR_H);
    text_on_black();
    gfx_PrintStringXY(top, MARGIN_X, 2);
    gfx_PrintStringXY(bottom, MARGIN_X, 240 - BAR_H + 2);
}

static void draw_doc(uint16_t scroll)
{
    char     buf[COLS + 2];
    char     status[COLS + 2];
    uint16_t i, last;
    uint8_t  len;
    const char *s;

    last = scroll + VISIBLE;
    if (last > doc.nlines)
        last = doc.nlines;
    sprintf(status, "%s %u/%u%s%s%s", cur_name, (unsigned)last, (unsigned)doc.nlines,
            doc.prev[0] ? " <" : "", doc.next[0] ? " >" : "", doc.truncated ? " (cut)" : "");
    draw_bars(doc.title[0] ? doc.title : cur_name, status);

    text_on_white();
    if (doc.nlines == 0)
        gfx_PrintStringXY("(empty page)", MARGIN_X, TOP_Y);
    for (i = 0; i < VISIBLE && scroll + i < doc.nlines; i++) {
        s = doc_line(&doc, scroll + i, &len);
        memcpy(buf, s, len);
        buf[len] = 0;
        gfx_PrintStringXY(buf, MARGIN_X, TOP_Y + i * LINE_H);
    }
}

static void draw_list(uint8_t count, uint8_t sel, uint8_t top, const char *msg)
{
    char    row[COLS + 2];
    uint8_t i, k;
    const char *s;

    draw_bars("HTML Reader", count ? "Enter/2nd: open   Clear: quit" : "Clear: quit");
    text_on_white();
    if (!count) {
        gfx_PrintStringXY("No pages found.", MARGIN_X, TOP_Y);
        gfx_PrintStringXY("Convert HTML with html2ce.py,", MARGIN_X, TOP_Y + 2 * LINE_H);
        gfx_PrintStringXY("then send the .8xv files to", MARGIN_X, TOP_Y + 3 * LINE_H);
        gfx_PrintStringXY("the calculator.", MARGIN_X, TOP_Y + 4 * LINE_H);
    }
    for (i = 0; i < LIST_ROWS && top + i < count; i++) {
        const Entry *e = &entries[top + i];
        int y = BAR_H + 2 + i * LIST_H;

        k = 0;
        for (s = e->name; *s && k < 8; s++)
            row[k++] = *s;
        while (k < 9)
            row[k++] = ' ';
        for (s = e->title; *s && k < COLS; s++)
            row[k++] = *s;
        row[k] = 0;
        if (top + i == sel) {
            gfx_SetColor(C_BLACK);
            gfx_FillRectangle(0, y - 1, 320, LIST_H);
            text_on_black();
        } else {
            text_on_white();
        }
        gfx_PrintStringXY(row, MARGIN_X, y);
    }
    if (msg) {
        text_on_white();
        gfx_PrintStringXY(msg, MARGIN_X, 240 - BAR_H - LINE_H - 2);
    }
}

/* ---- main --------------------------------------------------------------------- */

int main(void)
{
    enum { S_LIST, S_DOC } state = S_LIST;
    uint8_t     count, sel = 0, top = 0, ev;
    uint16_t    scroll = 0, max_scroll;
    bool        dirty = true;
    const char *msg = NULL;

    count = scan_pages();

    gfx_Begin();
    gfx_SetDrawBuffer();
    gfx_SetMonospaceFont(8);

    for (;;) {
        ev = key_events();

        if (state == S_LIST) {
            if (ev & K_CLEAR)
                break;
            if (count) {
                if ((ev & K_DOWN) && sel + 1 < count) { sel++; dirty = true; }
                if ((ev & K_UP) && sel > 0)           { sel--; dirty = true; }
                if (sel < top)
                    top = sel;
                if (sel >= top + LIST_ROWS)
                    top = sel - LIST_ROWS + 1;
                if (ev & (K_ENTER | K_2ND)) {
                    if (load_page(entries[sel].name)) {
                        state = S_DOC;
                        scroll = 0;
                        msg = NULL;
                    } else {
                        msg = "Could not open that page.";
                    }
                    dirty = true;
                }
            }
            if (dirty)
                draw_list(count, sel, top, msg);
        } else {
            max_scroll = doc.nlines > VISIBLE ? doc.nlines - VISIBLE : 0;
            if ((ev & K_DOWN) && scroll < max_scroll)  { scroll++; dirty = true; }
            if ((ev & K_UP) && scroll > 0)             { scroll--; dirty = true; }
            if (ev & K_2ND) {
                scroll = (scroll + VISIBLE - 1 > max_scroll) ? max_scroll : scroll + VISIBLE - 1;
                dirty = true;
            }
            if (ev & K_ALPHA) {
                scroll = (scroll > VISIBLE - 1) ? scroll - (VISIBLE - 1) : 0;
                dirty = true;
            }
            if ((ev & K_RIGHT) && doc.next[0]) {
                char name[9];

                strcpy(name, doc.next);
                if (load_page(name)) {
                    scroll = 0;
                } else {
                    state = S_LIST;
                    msg = "Could not open next page.";
                }
                dirty = true;
            }
            if ((ev & K_LEFT) && doc.prev[0]) {
                char name[9];

                strcpy(name, doc.prev);
                if (load_page(name)) {
                    scroll = 0;
                } else {
                    state = S_LIST;
                    msg = "Could not open previous page.";
                }
                dirty = true;
            }
            if (ev & K_CLEAR) {
                state = S_LIST;
                dirty = true;
            }
            if (dirty && state == S_DOC)
                draw_doc(scroll);
            else if (dirty)
                draw_list(count, sel, top, msg);
        }

        if (dirty) {
            gfx_SwapDraw();
            dirty = false;
        }
        delay(15);
    }

    gfx_End();
    return 0;
}
