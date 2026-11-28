#!/bin/bash
# Rebuild the two redistributable Intel media helpers from the release's source archives.
set -eu
if [[ $# != 2 || $(uname -sm) != 'Darwin x86_64' ]]; then
    echo "Usage (Intel macOS): $0 SOURCE_ARCHIVE_DIR OUTPUT_DIR" >&2; exit 2
fi
sources=$(cd "$1" && pwd -P)
mkdir -p "$2"
output=$(cd "$2" && pwd -P)
for name in ffmpeg ffprobe; do [[ ! -e "$output/$name" ]] || { echo "$output/$name exists" >&2; exit 1; }; done
ffmpeg=ffmpeg-9.1.tar.xz
lame=lame-4.0.tar.gz
(cd "$sources" && printf '%s  %s\n' \
    1a4d394ae63d190a526cd052eb4415c156b5c5e2df37efab41a7de0b8fde2433 "$ffmpeg" \
    3df5124d5ad3a98312ffd7ba6a9b36230e4f8a3e66d3ce0f425e336c32d216eb "$lame" | /usr/bin/shasum -a 256 -c -)
work=$(mktemp -d "${TMPDIR:-/tmp}/daw-media.XXXXXX")
cleanup() {
    if [[ $? == 0 ]]; then rm -rf "$work"; else echo "Build files retained: $work" >&2; fi
}
trap cleanup EXIT
/usr/bin/tar -xf "$sources/$ffmpeg" -C "$work"
/usr/bin/tar -xf "$sources/$lame" -C "$work"
prefix="$work/prefix"
jobs=$(/usr/sbin/sysctl -n hw.logicalcpu)
export MACOSX_DEPLOYMENT_TARGET=13.7
export CC=/usr/bin/clang CXX=/usr/bin/clang++
export SDKROOT=$(/usr/bin/xcrun --sdk macosx --show-sdk-path)
export PKG_CONFIG_PATH= PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig"
(cd "$work/lame-4.0" && ./configure --prefix="$prefix" --disable-shared --enable-static --disable-frontend --disable-decoder && make -j"$jobs" && make install)
# Only LAME and Apple SDK libraries can be linked in. Video output is stream-copied,
# and MP3, AAC, containers, subtitle conversion and native HTTP/TLS stay available.
(cd "$work/ffmpeg-9.1" && ./configure --prefix="$prefix" --cc="$CC" --disable-autodetect \
    --disable-debug --disable-doc --disable-ffplay --disable-x86asm \
    --disable-encoders --enable-encoder='libmp3lame,aac,alac,flac,opus,vorbis,pcm_*,srt,subrip,ass,webvtt,mov_text,mpeg2video,wrapped_avframe' \
    --disable-filters --enable-filter='buffer,abuffer,buffersink,abuffersink,null,anull,format,aformat,scale,aresample,trim,atrim,setpts,asetpts,apad,pan,volume,sine,testsrc2' \
    --disable-devices --enable-indev=lavfi \
    --enable-gpl --enable-version3 --enable-libmp3lame --enable-securetransport \
    --enable-zlib --enable-bzlib --enable-iconv --enable-audiotoolbox --enable-videotoolbox \
    --extra-cflags="-I$prefix/include" --extra-ldflags="-L$prefix/lib" --extra-libs=-liconv && make -j"$jobs" ffmpeg ffprobe)
for name in ffmpeg ffprobe; do
    binary="$work/ffmpeg-9.1/$name"
    /usr/bin/lipo "$binary" -verify_arch x86_64
    /usr/bin/otool -L "$binary" | awk 'NR>1 && $1 !~ /^\/(usr\/lib|System\/Library)\// {print "Non-system dependency:", $1; bad=1} END {exit bad}'
    /usr/bin/install -m 755 "$binary" "$output/$name"
done
/usr/bin/tar -xOf "$sources/$ffmpeg" ffmpeg-9.1/COPYING.GPLv3 > "$output/ffmpeg-GPLv3.txt"
/usr/bin/tar -xOf "$sources/$lame" lame-4.0/COPYING > "$output/lame-COPYING.txt"
/usr/bin/tar -xOf "$sources/$lame" lame-4.0/LICENSE > "$output/lame-LICENSE.txt"
