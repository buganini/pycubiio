#!/bin/sh
# Download original Cubiio (1st gen) vendor files from Muherz and verify SHA256.
# These files are proprietary (Muherz) and are not redistributed in this repo.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEST="$ROOT/vendor/cubiio_1"
BASE="https://cubiio1.muherz.com/files"

if command -v sha256sum >/dev/null 2>&1; then
    sha256() { sha256sum "$1" | cut -d' ' -f1; }
else
    sha256() { shasum -a 256 "$1" | cut -d' ' -f1; }
fi

# <relative dest path> <url> <sha256>
FILES="
apk/cubiio1_3_3.apk|$BASE/cubiio1_3_3.apk|90c8302cd699196d1c630b3da6e0148c3083107f1af745257a004b4a03a8729e
firmware/v1_11e.zip|$BASE/firmware/v1_11e.zip|6182efc1cf904a9159a76108cf8e922b3031adeedd31a38ec8d1eacdd2b33dfb
updater/mac_updater_install.dmg|$BASE/firmware/mac_updater_install.dmg|88e9e20a0aad0806b6461e265ea790ae08e547f40c19acd898f8d190a5b0c84c
updater/cubiio-updater-catalina.app.zip|$BASE/firmware/cubiio-updater-catalina.app.zip|51fff368c475652ce1981a7254551a7332cfbc1aebc54e3814b925e7318bae4a
updater/win_updater_install.exe|$BASE/firmware/win_updater_install.exe|9bfa2ca44580e98dd93dff50b850f1485ee765b8dd1583fbb3370d5e373cd8e1
"

# Optional filter: only fetch entries whose path contains one of the args
# e.g. scripts/fetch_vendor.sh apk firmware
want() {
    [ $# -le 1 ] && return 0
    p=$1; shift
    for k in "$@"; do
        case "$p" in *"$k"*) return 0 ;; esac
    done
    return 1
}

fail=0
for line in $FILES; do
    path=${line%%|*}; rest=${line#*|}
    url=${rest%%|*}; sum=${rest#*|}
    want "$path" "$@" || continue

    out="$DEST/$path"
    if [ -f "$out" ] && [ "$(sha256 "$out")" = "$sum" ]; then
        echo "ok    $path"
        continue
    fi

    mkdir -p "$(dirname "$out")"
    echo "fetch $path"
    curl -fL --retry 3 -o "$out.part" "$url"
    got=$(sha256 "$out.part")
    if [ "$got" = "$sum" ]; then
        mv "$out.part" "$out"
        echo "ok    $path"
    else
        echo "FAIL  $path: sha256 $got != $sum" >&2
        rm -f "$out.part"
        fail=1
    fi
done

exit $fail
