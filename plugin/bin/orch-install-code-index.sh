#!/usr/bin/env bash
# orch-install-code-index.sh — install the pinned orch-index release binary (macOS, Linux, Windows under Git Bash).
# Usage: orch-install-code-index.sh [--dest DIR] [--dry-run] [--print-target]   (default DIR: ${ORCH_DATA:-$HOME/.claude/orch-data}/bin)
# Trust root: the SHA-256 pins below, committed in this repo; the release's own SHA256SUMS is not used.
# Never overwrites an existing <DIR>/orch-index (Windows: orch-index.exe), never uses sudo, refuses to run as root.
# ORCH_INDEX_AUTOINSTALL=0 disables it (exit 3, checked first, no network).
# Exit: 0 installed or already there, 1 failure, 2 bad usage, 3 unsupported platform or disabled.
set -euo pipefail

# --- pins (begin) ---
readonly ORCH_INDEX_VERSION=v0.2.0
readonly SHA256_AARCH64_APPLE_DARWIN=f9694a720b1943bba933cd96411de6e69835ff720cab5846c91c06fdafc34151
readonly SHA256_X86_64_APPLE_DARWIN=b67ac28da7a00577c4b46ff06a6eb8abbd6a0d694377bc2e2d2b53bf04dd15b3
readonly SHA256_X86_64_UNKNOWN_LINUX_MUSL=b6480490d496221e9086742e1de6f5dd61dbb61549cfa7a94f3a56c7846c542e
readonly SHA256_AARCH64_UNKNOWN_LINUX_MUSL=e2597adde9b9cf5b8bcb19aaad5a4baca9ea6142f0e67efb8449c01b8c0711c9
readonly SHA256_X86_64_PC_WINDOWS_MSVC=89bbcc0094f964bddf61cb5d91fcfaa87caa60819d37d8c80c845cf872719146
# --- pins (end) ---
# Bundled binaries (bin/vendor/orch-index-<target>, `.exe` on Windows) are pinned by the binary's own SHA-256 and win
# over the download.
# --- bundled pins (begin) ---
readonly BUNDLED_SHA256_AARCH64_APPLE_DARWIN=baa6558260e2086815cf293881b5ada8e46e33ca49eb975933dedd62e284aa60
readonly BUNDLED_SHA256_X86_64_APPLE_DARWIN=7255d9d5ecdf267fc4ceda7bad9b76e81632dd0fcdfb3793659ab1acd5b8a85a
readonly BUNDLED_SHA256_X86_64_UNKNOWN_LINUX_MUSL=ac4255a5ff60800f8857ee8546cd58de320ef2507f38eace899a0d6c62113f77
readonly BUNDLED_SHA256_AARCH64_UNKNOWN_LINUX_MUSL=85001aca34fa1ee50b0844b41557dd0ce9f06ae339fec6f0888ade77c5e759cc
readonly BUNDLED_SHA256_X86_64_PC_WINDOWS_MSVC=b419788091320b4220f9fd0afee9e7ed56d88bc0fca7d9094d1b7f5f19fb9dd2
# --- bundled pins (end) ---

readonly RELEASE_BASE=https://github.com/ijat/orch-index/releases/download
readonly MANUAL_URL=https://github.com/ijat/orch-index#install
readonly MAX_BYTES=67108864   # 64 MiB, archive download limit

usage() { sed -n 3p "$0" | sed 's/^# //' >&2; exit 2; }

dest="${ORCH_DATA:-$HOME/.claude/orch-data}/bin"; dry_run=0; print_target=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) [ $# -ge 2 ] && [ -n "$2" ] || usage; dest="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    --print-target) print_target=1; shift ;;
    -h|--help) sed -n 2,7p "$0" | sed 's/^# //'; exit 0 ;;
    *) usage ;;
  esac
done

fail() { echo "failed: $*"; exit 1; }

if [ "${ORCH_INDEX_AUTOINSTALL-}" = 0 ]; then echo "disabled by ORCH_INDEX_AUTOINSTALL"; exit 3; fi

[ "$(id -u)" != 0 ] || fail "refusing to run as root"

os=$(uname -s); arch=$(uname -m)
case "$os/$arch" in
  Darwin/arm64|Darwin/aarch64) target=aarch64-apple-darwin; pin=$SHA256_AARCH64_APPLE_DARWIN ;;
  Darwin/x86_64)               target=x86_64-apple-darwin;  pin=$SHA256_X86_64_APPLE_DARWIN ;;
  Linux/x86_64|Linux/amd64)    target=x86_64-unknown-linux-musl;  pin=$SHA256_X86_64_UNKNOWN_LINUX_MUSL ;;
  Linux/aarch64|Linux/arm64)   target=aarch64-unknown-linux-musl; pin=$SHA256_AARCH64_UNKNOWN_LINUX_MUSL ;;
  MINGW*/x86_64|MSYS*/x86_64|CYGWIN*/x86_64) target=x86_64-pc-windows-msvc; pin=$SHA256_X86_64_PC_WINDOWS_MSVC ;;
  *) target="" ; pin="" ;;
esac
# Windows ships a zip holding orch-index.exe and installs as orch-index.exe; every other target a tar.gz.
exe=""; ext=tar.gz; case "$target" in *-windows-*) exe=.exe; ext=zip ;; esac

if [ "$print_target" = 1 ]; then
  [ -n "$target" ] || { echo "unsupported platform $os/$arch"; exit 3; }
  echo "$target"; exit 0
fi

final="$dest/orch-index$exe"
if [ -e "$final" ] || [ -L "$final" ]; then echo "already installed $final"; exit 0; fi

if [ -z "$target" ]; then
  echo "install by hand: $MANUAL_URL"
  echo "unsupported platform $os/$arch"; exit 3
fi
echo "target $target"

case "$pin" in
  [0-9a-f]*) [ ${#pin} = 64 ] && [ -z "${pin//[0-9a-f]/}" ] || fail "malformed pinned checksum for $target" ;;
  *) fail "no pinned checksum for $target $ORCH_INDEX_VERSION (install by hand: $MANUAL_URL)" ;;
esac

base=$RELEASE_BASE; proto='=https'
if [ "${ORCH_INSTALL_TEST:-}" = 1 ] && [ -n "${ORCH_INSTALL_TEST_BASE:-}" ]; then
  # Test hook: a local file:// tree only; every other check, the hash included, still applies.
  case "$ORCH_INSTALL_TEST_BASE" in file:///*) ;; *) fail "test base must be a file:/// URL" ;; esac
  base=$ORCH_INSTALL_TEST_BASE; proto='=file'
fi
archive_name="orch-index-$ORCH_INDEX_VERSION-$target.$ext"
url="$base/$ORCH_INDEX_VERSION/$archive_name"

if [ "$dry_run" = 1 ]; then
  echo "download $url"
  echo "dry-run: would install $final $ORCH_INDEX_VERSION"; exit 0
fi

bundled=""; bundled_pin=""
case "$target" in
  aarch64-apple-darwin)       bundled_pin=$BUNDLED_SHA256_AARCH64_APPLE_DARWIN ;;
  x86_64-apple-darwin)        bundled_pin=$BUNDLED_SHA256_X86_64_APPLE_DARWIN ;;
  x86_64-unknown-linux-musl)  bundled_pin=$BUNDLED_SHA256_X86_64_UNKNOWN_LINUX_MUSL ;;
  aarch64-unknown-linux-musl) bundled_pin=$BUNDLED_SHA256_AARCH64_UNKNOWN_LINUX_MUSL ;;
  x86_64-pc-windows-msvc)     bundled_pin=$BUNDLED_SHA256_X86_64_PC_WINDOWS_MSVC ;;
esac
cand="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vendor/orch-index-$target$exe"
use_bundled=0
if [ -n "$bundled_pin" ] && [ -f "$cand" ] && [ ! -L "$cand" ]; then use_bundled=1; fi

if [ "$use_bundled" = 0 ]; then command -v curl >/dev/null 2>&1 || fail "curl not found"; fi
py=$(command -v python3 || true); unzip_bin=$(command -v unzip || true)
if [ "$use_bundled" = 0 ]; then
  if [ "$ext" = zip ]; then
    [ -n "$unzip_bin" ] || [ -n "$py" ] || fail "neither unzip nor python3 found (install by hand: $MANUAL_URL)"
  else
    [ -n "$py" ] || fail "python3 not found"
  fi
fi
if command -v shasum >/dev/null 2>&1; then sha() { shasum -a 256 "$1"; }
elif command -v sha256sum >/dev/null 2>&1; then sha() { sha256sum "$1"; }
else fail "shasum or sha256sum not found"; fi

if [ -e "$dest" ] && [ ! -d "$dest" ]; then fail "$dest exists and is not a directory"; fi

tmp=""; staged=""
cleanup() { [ -z "$staged" ] || rm -f "$staged"; [ -z "$tmp" ] || rm -rf "$tmp"; }
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
tmp=$(mktemp -d "${TMPDIR:-/tmp}/orch-install-code-index.XXXXXX") || fail "cannot create a temp dir"
archive="$tmp/$archive_name"

if [ "$use_bundled" = 1 ]; then
  got=$(sha "$cand" | awk '{print $1}')
  [ "$got" = "$bundled_pin" ] || fail "bundled binary checksum mismatch"
  echo "bundled $cand (checksum ok)"
  cp "$cand" "$tmp/orch-index" || fail "cannot copy the bundled binary"
else

echo "download $url"
# -q first: ignore any ~/.curlrc. Redirects (GitHub -> its CDN) must stay on the same protocol.
curl -q -fsSL --proto "$proto" --proto-redir "$proto" --tlsv1.2 --max-filesize "$MAX_BYTES" \
  --connect-timeout 30 --max-time 600 -o "$archive" "$url" || fail "download failed"
size=$(wc -c < "$archive" | tr -d ' ')
[ "$size" -le "$MAX_BYTES" ] || fail "download larger than $MAX_BYTES bytes"

got=$(sha "$archive" | awk '{print $1}')
[ "$got" = "$pin" ] || fail "checksum mismatch"
echo "checksum ok"

if command -v gh >/dev/null 2>&1; then
  if GH_PROMPT_DISABLED=1 gh attestation verify "$archive" --repo ijat/orch-index >/dev/null 2>&1; then
    echo "attested"
  else
    echo "not attested (gh attestation verify failed)"
    echo "warning: installing on the pinned checksum alone"
  fi
else
  echo "not attested (gh not found)"
fi

if [ "$ext" = zip ]; then
# The Windows zip: take the one member orch-index.exe by its exact name (nothing else is extracted, so no
# archive path, mode or link is used), refuse it when absent or listed twice, and cap its size.
zip_member="orch-index-$ORCH_INDEX_VERSION-$target/orch-index.exe"; zip_limit=268435456
if [ -n "$unzip_bin" ]; then
  names=$("$unzip_bin" -Z1 "$archive" 2>/dev/null) || fail "unreadable archive"
  [ "$(printf '%s\n' "$names" | grep -Fxc -- "$zip_member")" = 1 ] || fail "binary missing from archive or listed twice"
  (umask 077; "$unzip_bin" -p "$archive" "$zip_member" | head -c $((zip_limit + 1)) > "$tmp/orch-index") \
    || fail "unreadable archive"
  zsize=$(wc -c < "$tmp/orch-index" | tr -d ' ')
  [ "$zsize" -gt 0 ] || fail "short binary member"
  [ "$zsize" -le "$zip_limit" ] || fail "binary larger than $zip_limit bytes"
else
"$py" -I - "$archive" "$tmp" "$zip_member" "$zip_limit" <<'PY' || exit 1
import os, sys, zipfile
archive, out, member, limit = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])

def die(msg):
    print("failed: " + msg)
    sys.exit(1)

try:
    zf = zipfile.ZipFile(archive)
    infos = [i for i in zf.infolist() if i.filename == member]
except (zipfile.BadZipFile, OSError, EOFError, ValueError):
    die("unreadable archive")
if len(infos) != 1:
    die("binary missing from archive or listed twice")
info = infos[0]
if info.file_size == 0:
    die("short binary member")
if info.file_size > limit:
    die("binary larger than %d bytes" % limit)
fd = os.open(os.path.join(out, "orch-index"),
             os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o700)
total = 0
with zf.open(info) as src, os.fdopen(fd, "wb") as dst:
    while True:
        chunk = src.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            die("binary larger than %d bytes" % limit)
        dst.write(chunk)
if total != info.file_size:
    die("short binary member")
PY
fi
echo "archive ok"
else
# List every member before extracting; accept only the binary (plus its directory and a licence or
# readme beside it); write the binary's bytes to a new file ourselves, so no archive mode or link is used.
"$py" -I - "$archive" "$tmp" "$ORCH_INDEX_VERSION" "$target" <<'PY' || exit 1
import os, re, sys, tarfile
archive, out, version, target = sys.argv[1:5]
top = "orch-index-%s-%s" % (version, target)
binary = top + "/orch-index"
extra = re.compile(r"(LICEN[CS]E|COPYING|README)([-.][A-Za-z0-9]+)*\Z")
limit = 256 * 1024 * 1024

def die(msg):
    print("failed: " + msg)
    sys.exit(1)

def rejected(name, why):
    die("archive member rejected: %s (%s)" % (ascii(name)[1:-1][:200], why))

try:
    tf = tarfile.open(archive, mode="r:gz")
    members = tf.getmembers()
except (tarfile.TarError, OSError, EOFError, ValueError):
    die("unreadable archive")

found = None
for m in members:
    name = m.name
    parts = name.split("/")
    if name.startswith("/"):
        rejected(name, "absolute path")
    if ".." in parts:
        rejected(name, "parent directory")
    if m.issym():
        rejected(name, "symlink")
    if m.islnk():
        rejected(name, "hardlink")
    if m.ischr() or m.isblk() or m.isfifo():
        rejected(name, "device or fifo")
    if not (m.isreg() or m.isdir()):
        rejected(name, "unsupported type")
    if m.isdir():
        if name.rstrip("/") == top:
            continue
        rejected(name, "unexpected directory")
    if name == binary:
        if found is not None:
            rejected(name, "duplicate binary")
        found = m
        continue
    if len(parts) == 2 and parts[0] == top and extra.match(parts[1]):
        continue
    rejected(name, "unexpected member")

if found is None:
    die("binary missing from archive")
if found.size > limit:
    die("binary larger than %d bytes" % limit)
src = tf.extractfile(found)
if src is None:
    die("unreadable binary member")
fd = os.open(os.path.join(out, "orch-index"),
             os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o700)
total = 0
with os.fdopen(fd, "wb") as dst:
    while True:
        chunk = src.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            die("binary larger than %d bytes" % limit)
        dst.write(chunk)
if total != found.size:
    die("short binary member")
print("archive ok")
PY
fi
fi

if [ ! -d "$dest" ]; then (umask 022 && mkdir -p "$dest") || fail "cannot create $dest"; fi
staged=$(mktemp "$dest/.orch-index.XXXXXX") || fail "cannot write to $dest"
if [ -n "$exe" ]; then   # Windows runs a file only under an .exe name
  mv "$staged" "$staged$exe" || fail "cannot write to $dest"; staged="$staged$exe"
fi
install -m 755 "$tmp/orch-index" "$staged" || fail "cannot write to $dest"

# Run the version check on the staged file in <dest>, not in the temp dir (which may be mounted noexec);
# a failure removes the staged file (cleanup trap).
want=${ORCH_INDEX_VERSION#v}
ver_out=$("$staged" --version 2>/dev/null) || fail "binary does not run"
ok=0
read -r -a words <<< "$(printf '%s' "$ver_out" | tr '\n' ' ')"
for word in ${words[@]+"${words[@]}"}; do [ "$word" = "$want" ] && ok=1; done
[ "$ok" = 1 ] || fail "binary reports a version other than $want"
echo "version ok $want"
if [ -e "$final" ] || [ -L "$final" ]; then echo "already installed $final"; exit 0; fi
mv -n "$staged" "$final" 2>/dev/null || true
if [ -e "$staged" ]; then
  if [ -e "$final" ] || [ -L "$final" ]; then echo "already installed $final"; exit 0; fi
  fail "cannot move the binary into $dest"
fi
staged=""
echo "installed $final $ORCH_INDEX_VERSION"
