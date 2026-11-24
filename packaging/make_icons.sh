#!/bin/sh
# Render packaging/icon.svg into the macOS .icns (and a Windows .ico for later).
set -e
cd "$(dirname "$0")"
OUT=build/icons; rm -rf "$OUT"; mkdir -p "$OUT/Gabe.iconset"
magick -background none -density 384 icon.svg -resize 1024x1024 "$OUT/icon_1024.png"
for s in 16 32 128 256 512; do
  magick "$OUT/icon_1024.png" -resize ${s}x${s} "$OUT/Gabe.iconset/icon_${s}x${s}.png"
  magick "$OUT/icon_1024.png" -resize $((s*2))x$((s*2)) "$OUT/Gabe.iconset/icon_${s}x${s}@2x.png"
done
iconutil -c icns "$OUT/Gabe.iconset" -o "$OUT/Gabe.icns"
magick "$OUT/icon_1024.png" -define icon:auto-resize=256,128,64,48,32,16 "$OUT/Gabe.ico"
echo "$OUT/Gabe.icns"
