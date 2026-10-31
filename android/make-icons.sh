#!/usr/bin/env bash
# Render the Android launcher icons (and, if present, splash images) for one app from
# shared/icons/<app>.svg. Usage: android/make-icons.sh passenger|driver <path to app/src/main/res>
# Needs rsvg-convert and ImageMagick (magick). Re-run after changing the icon SVGs.
set -euo pipefail
app="$1"
res="$2"
root="$(dirname "$(readlink -f "$0")")/.."
case "$app" in
  passenger) color="#2e7d32" ;;
  driver) color="#1565c0" ;;
  *) echo "Usage: $0 passenger|driver <res dir>" >&2; exit 2 ;;
esac
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Glyph only on transparent ground, scaled into the adaptive-icon safe zone (66 of 108 dp).
sed -E 's|<rect width="64" height="64" rx="14" fill="[^"]*"/>||; s|<svg ([^>]*)>|<svg \1><g transform="translate(32 32) scale(0.62) translate(-32 -32)">|; s|</svg>|</g></svg>|' \
  "$root/shared/icons/$app.svg" >"$tmp/foreground.svg"

declare -A legacy=([mdpi]=48 [hdpi]=72 [xhdpi]=96 [xxhdpi]=144 [xxxhdpi]=192)
for d in "${!legacy[@]}"; do
  s=${legacy[$d]}
  mkdir -p "$res/mipmap-$d"
  rsvg-convert -w "$s" -h "$s" "$root/shared/icons/$app.svg" -o "$res/mipmap-$d/ic_launcher.png"
  rsvg-convert -w "$s" -h "$s" "$root/shared/icons/$app.svg" -o "$tmp/sq.png"
  magick "$tmp/sq.png" \( -size "${s}x${s}" xc:none -fill white -draw "circle $((s / 2)),$((s / 2)) $((s / 2)),0" \) \
    -compose DstIn -composite "$res/mipmap-$d/ic_launcher_round.png"
  f=$((s * 108 / 48))
  rsvg-convert -w "$f" -h "$f" "$tmp/foreground.svg" -o "$res/mipmap-$d/ic_launcher_foreground.png"
done

mkdir -p "$res/mipmap-anydpi-v26" "$res/values"
for name in ic_launcher ic_launcher_round; do
  cat >"$res/mipmap-anydpi-v26/$name.xml" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background"/>
    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>
</adaptive-icon>
EOF
done
cat >"$res/values/ic_launcher_background.xml" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="ic_launcher_background">${color^^}</color>
</resources>
EOF

# Capacitor splash screens: replace the default artwork with the app icon on a plain background.
find "$res" -name splash.png | while read -r splash; do
  size=$(magick identify -format '%wx%h' "$splash")
  short=$(magick identify -format '%[fx:min(w,h)]' "$splash")
  rsvg-convert -w $((short / 3)) -h $((short / 3)) "$root/shared/icons/$app.svg" -o "$tmp/logo.png"
  magick -size "$size" xc:"#f3f5f4" "$tmp/logo.png" -gravity center -composite "$splash"
done
echo "Icons written to $res"
