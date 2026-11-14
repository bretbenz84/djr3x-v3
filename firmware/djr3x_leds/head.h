// Head strip: two eyes and the 80-pixel mouth.
#pragma once

namespace head {
void begin();                    // register the strip and blank it
void command(const char *cmd);   // one command line, prefix already removed
void tick();                     // call every pass of loop()
}
