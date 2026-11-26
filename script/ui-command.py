"""Send UI-automation commands to a running Pi GPUI and print the results.

    python -I script/ui-command.py <automation-folder> "click 100 232" "wait 500" "shot C:\tmp\a.png"

The app must have been started with PI_GPUI_AUTOMATION=<automation-folder>.
See docs/WINDOWS_UI_CHECKS.md.
"""
import os
import sys
import time

folder = sys.argv[1]
os.makedirs(folder, exist_ok=True)
done = os.path.join(folder, "done")
if os.path.exists(done):
    os.remove(done)
staged = os.path.join(folder, "command.tmp")
with open(staged, "w", encoding="utf-8") as file:
    file.write("\n".join(sys.argv[2:]) + "\n")
os.replace(staged, os.path.join(folder, "command"))  # the app reads `command`
for _ in range(600):
    if os.path.exists(done):
        time.sleep(0.05)
        print(open(done, encoding="utf-8").read(), end="")
        break
    time.sleep(0.1)
else:
    sys.exit("no answer: is the app running with PI_GPUI_AUTOMATION set to this folder?")
