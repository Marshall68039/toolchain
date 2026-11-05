#pragma once
#include <stdint.h>
extern volatile uint16_t kb_Data[8];
void kb_Scan(void);
#define kb_Down 0x01
#define kb_Left 0x02
#define kb_Right 0x04
#define kb_Up 0x08
#define kb_Enter 0x01
#define kb_Clear 0x40
#define kb_2nd 0x20
#define kb_Alpha 0x20
