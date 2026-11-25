#!/bin/sh
# Double-click on macOS: runs the builder in Terminal and keeps the window open at the end.
cd "$(dirname "$0")"
sh ./hld-vita-builder.sh "$@"
status=$?
printf '\nPress Enter to close this window.'
read -r _
exit $status
