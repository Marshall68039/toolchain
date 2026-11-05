#pragma once
#include <stdint.h>
void gfx_Begin(void); void gfx_End(void);
void gfx_SetDrawBuffer(void); void gfx_SwapDraw(void);
void gfx_SetMonospaceFont(uint8_t spacing);
void gfx_FillScreen(uint8_t color); void gfx_SetColor(uint8_t color);
void gfx_FillRectangle(int x, int y, int w, int h);
void gfx_SetTextFGColor(uint8_t c); void gfx_SetTextBGColor(uint8_t c);
void gfx_SetTextTransparentColor(uint8_t c);
void gfx_PrintStringXY(const char *s, int x, int y);
