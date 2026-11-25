#!/bin/sh
# HLD Vita Builder (macOS; started by "HLD Vita Builder.command"). Uses Python 3.12+ if installed, otherwise downloads a pinned standalone Python
# (python-build-standalone 3.12.16, checksummed) into deps/python. Then runs scripts/kit_build.py.
set -e
cd "$(dirname "$0")"
if [ ! -f scripts/kit_build.py ] || [ ! -f kit.json ]; then
  echo "This is the launcher template from the source repository, not the builder kit."
  echo "Use the HLD-Vita-Builder zip from the project's Releases page (or make one with: python3 scripts/make_kit.py)."
  exit 1
fi
REL=https://github.com/astral-sh/python-build-standalone/releases/download/20261026
pick() {
  for candidate in deps/python/bin/python3 python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
      echo "$candidate"; return 0
    fi
  done
  return 1
}
if ! PY=$(pick); then
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64)  F=cpython-3.12.16+20261026-aarch64-apple-darwin-install_only.tar.gz;  SUM=fbf4afef53c2a6b72bb741bc06d5671a9dfaf87bd84e5aa73ac9b3d0d589f14e ;;
    Darwin-x86_64) F=cpython-3.12.16+20261026-x86_64-apple-darwin-install_only.tar.gz;   SUM=a962e47df522889eaa7ff0d6a56203f1079f68aabb65ffdbe8455f362bc769f3 ;;
    *) echo "This builder supports Windows and macOS. On $(uname -s) $(uname -m), install Python 3.12+ and try: python3 scripts/kit_build.py"; exit 1 ;;
  esac
  echo "Downloading Python 3.12 (first run only)..."
  mkdir -p deps/downloads
  curl -fL --progress-bar -o "deps/downloads/$F" "$REL/$(printf '%s' "$F" | sed 's/+/%2B/')"
  GOT=$(shasum -a 256 "deps/downloads/$F" | cut -d' ' -f1)
  [ "$GOT" = "$SUM" ] || { echo "Checksum mismatch for the Python download"; rm -f "deps/downloads/$F"; exit 1; }
  rm -rf deps/python && tar -xzf "deps/downloads/$F" -C deps
  PY=deps/python/bin/python3
fi
"$PY" scripts/kit_build.py "$@"
