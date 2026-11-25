#!/bin/bash
# Copy the game you built to your Vita, over Wi-Fi or a USB cable. Run "HLD Vita Builder.command" first.
cd "$(dirname "$0")" || exit 1
if [ ! -f kit.json ]; then
  echo "This is the launcher template from the source repository, not the builder kit."
  echo "Use the HLD-Vita-Builder zip from the project's Releases page."
  read -r -p "Press Enter to close."
  exit 1
fi
PY=deps/venv/bin/python
[ -x "$PY" ] || PY=deps/python/bin/python3
[ -x "$PY" ] || PY=$(command -v python3)
if [ -z "$PY" ]; then
  echo "Run \"HLD Vita Builder.command\" first."
  read -r -p "Press Enter to close."
  exit 1
fi
echo "Wi-Fi: on the Vita open VitaShell, press SELECT to start FTP, and type the IP address it shows."
echo "USB:   on the Vita open VitaShell, press START, set \"USB device\" to Memory Card, go back,"
echo "       press SELECT and connect the cable. Then type usb."
echo
read -r -p "IP address or usb: " TARGET
if [ -n "$TARGET" ]; then
  if [ "$(printf '%s' "$TARGET" | tr '[:upper:]' '[:lower:]')" = usb ]; then
    "$PY" scripts/install.py --usb
  else
    "$PY" scripts/install.py "$TARGET"
  fi
fi
echo
read -r -p "Press Enter to close."
