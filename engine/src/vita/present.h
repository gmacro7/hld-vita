#ifndef _BS_VITA_PRESENT_H_
#define _BS_VITA_PRESENT_H_

#include <stdbool.h>

void VitaPresent_start(void (*submit)(void), void (*finished)(void));
bool VitaPresent_queue(int frame);
void VitaPresent_stop(void);

#endif
