#pragma once
#include <stdint.h>
char *ti_Detect(void **vat_ptr, const char *detect_string);
uint8_t ti_Open(const char *name, const char *mode);
void *ti_GetDataPtr(uint8_t handle);
uint16_t ti_GetSize(uint8_t handle);
int ti_Close(uint8_t handle);
