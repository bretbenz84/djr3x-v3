// Chest strip: the three control panels and the two flickering chest lights.
#pragma once

namespace chest {
void begin();                    // register the strip and blank it
void command(const char *cmd);   // one command line, prefix already removed
void tick();                     // call every pass of loop()
}
