"""The Bluetooth stack's messages become one of our own log lines, at most every 30 s (November 20,
2026). Compiles the firmware's bt_log.cpp with a fake clock and log. No I/O."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / "djr3x_motion"
HARNESS = r'''
#include <cstdio>
#include "bt_log.h"
extern "C" void uni_log(const char* fmt, ...);
void emit_log(const char* lvl, const char* msg) { std::printf("%s|%s\n", lvl, msg); }
int main() {
  bt_log_service(1000);                                   // Nothing said: nothing reported.
  for (int i = 0; i < 3; i++) uni_log("Failed to disconnect HIDS client for hids_cid=%d, status=%#x\n", 0, 2);
  bt_log_service(2000);                                   // The first report comes at once...
  for (int i = 0; i < 40; i++) uni_log("Failed to disconnect HIDS client for hids_cid=%d, status=%#x\n", 0, 2);
  bt_log_service(12000);                                  // ...then not again inside thirty seconds,
  bt_log_service(2000 + BT_LOG_EVERY_MS);                 // and then with the count since.
  bt_log_service(2000 + 2 * BT_LOG_EVERY_MS);             // Quiet since: nothing more.
}
'''


class BluetoothLogTests(unittest.TestCase):
    def test_messages_are_counted_and_reported_as_one_line_at_a_time(self):
        with tempfile.TemporaryDirectory(prefix="rex-bt-log-") as directory:
            work = Path(directory)
            for name in ("bt_log.cpp", "bt_log.h"):
                shutil.copyfile(FW / name, work / name)
            (work / "Arduino.h").write_text("#pragma once\n#include <stdint.h>\n")
            (work / "proto_io.h").write_text("#pragma once\nvoid emit_log(const char* lvl, const char* msg);\n")
            (work / "harness.cpp").write_text(HARNESS)
            subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(work), str(work / "bt_log.cpp"),
                            str(work / "harness.cpp"), "-o", str(work / "bt")], check=True, capture_output=True, text=True)
            out = subprocess.run([str(work / "bt")], capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(out, [
            "info|bluetooth (3 since the last report): Failed to disconnect HIDS client for hids_cid=0, status=0x2 ",
            "info|bluetooth (40 since the last report): Failed to disconnect HIDS client for hids_cid=0, status=0x2 "])


if __name__ == "__main__":
    unittest.main()
