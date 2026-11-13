"""bin/orch-install-code-index.sh: the pinned, hash-verified orch-index installer.

No real network: every run gets a throwaway HOME and TMPDIR and a PATH whose first entry holds stubs
(`uname`, `gh`, `curl`; `id` for the root case). The `curl` stub logs its arguments and either runs the
real curl (file:// URLs only, via ORCH_INSTALL_TEST_BASE) or refuses. Runs use a copy of the script whose
pin block is rewritten to the test archive's hash; `test_pins_are_real` checks the shipped pins, and
`test_real_v020_archives` (needs ORCH_INSTALL_REAL_ARCHIVES_DIR) runs the shipped script, pins untouched,
on the real release archives. The `test_mutant_*` tests remove one guard from a copy of the script and
check that the guard's own test then fails."""
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import warnings
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "bin")
SCRIPT = os.path.join(BIN, 'orch-install-code-index.sh')
SYS_PATH = '/usr/bin:/bin:/usr/sbin:/sbin'
REAL_CURL = shutil.which('curl', path=SYS_PATH)
VERSION = 'v0.2.0'
TARGET = 'aarch64-apple-darwin'
TOP = 'orch-index-%s-%s' % (VERSION, TARGET)
ARCHIVE = TOP + '.tar.gz'
GOOD_BIN = b'#!/bin/sh\necho "orch-index 0.2.0"\n'
TARGETS = ('AARCH64_APPLE_DARWIN', 'X86_64_APPLE_DARWIN', 'X86_64_UNKNOWN_LINUX_MUSL',
           'AARCH64_UNKNOWN_LINUX_MUSL', 'X86_64_PC_WINDOWS_MSVC')
WIN = 'x86_64-pc-windows-msvc'          # Git for Windows bash; its release asset is a zip holding orch-index.exe
PLATFORMS = {'aarch64-apple-darwin': ('Darwin', 'arm64'), 'x86_64-apple-darwin': ('Darwin', 'x86_64'),
             'x86_64-unknown-linux-musl': ('Linux', 'x86_64'), 'aarch64-unknown-linux-musl': ('Linux', 'aarch64'),
             WIN: ('MINGW64_NT-10.0-26100', 'x86_64')}
VENDOR = os.path.join(BIN, 'vendor')


def exe_name(target):
    return 'orch-index.exe' if target == WIN else 'orch-index'


def archive_ext(target):
    return 'zip' if target == WIN else 'tar.gz'
CURL_FLAGS = ('-q', '--proto', '--proto-redir', '--tlsv1.2', '--max-filesize', '67108864')


def make_tar(path, members):
    """members: (name, kind, payload); kind is 'file', 'dir', 'sym' or 'link' (payload = link target)."""
    with tarfile.open(path, 'w:gz') as tf:
        for name, kind, payload in members:
            ti = tarfile.TarInfo(name)
            if kind == 'file':
                ti.size, ti.mode = len(payload), 0o755
                tf.addfile(ti, io.BytesIO(payload))
                continue
            ti.type = {'dir': tarfile.DIRTYPE, 'sym': tarfile.SYMTYPE, 'link': tarfile.LNKTYPE}[kind]
            ti.mode = 0o755
            if kind in ('sym', 'link'):
                ti.linkname = payload
            tf.addfile(ti)


def make_zip(path, members):
    """members: (name, payload) pairs, in order (a name may repeat)."""
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')          # zipfile warns on a duplicate name, which a test wants
        with zipfile.ZipFile(path, 'w') as zf:
            for name, payload in members:
                zf.writestr(name, payload)


def pin_var(target):
    return target.upper().replace('-', '_')


def sha256(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


STUBS = {
    'uname': '#!/bin/sh\ncase "$1" in -s) echo "${FAKE_OS:-Darwin}" ;; -m) echo "${FAKE_ARCH:-arm64}" ;; '
             '*) echo "${FAKE_OS:-Darwin}" ;; esac\n',
    'gh': '#!/bin/sh\necho "$@" >> "$GH_LOG"\nexit "${FAKE_GH_RC:-1}"\n',
    # FAKE_CURL_NOMAX=1 drops `--max-filesize N`, standing in for a transfer curl cannot cap up front.
    'curl': '#!/bin/sh\nprintf "%%s\\n" "$@" >> "$CURL_LOG"\n'
            '[ "${FAKE_CURL_BLOCK:-0}" = 1 ] && exit 99\n'
            'if [ "${FAKE_CURL_NOMAX:-0}" = 1 ]; then skip=0\n'
            '  for a; do shift; if [ "$skip" = 1 ]; then skip=0; continue; fi\n'
            '    if [ "$a" = --max-filesize ]; then skip=1; continue; fi; set -- "$@" "$a"; done\nfi\n'
            'exec "%s" "$@"\n' % REAL_CURL,
    'id': '#!/bin/sh\necho 0\n',
    # Runs the installer's Python block with every archive member reading one byte short of its header size.
    'python3-short': '#!/bin/sh\n[ "$1" = -I ] && [ "$2" = - ] || exec "%s" "$@"\nshift 2\nexec "%s" -I -c \''
                     'import sys, tarfile\n'
                     '_orig = tarfile.TarFile.extractfile\n'
                     'class Short:\n'
                     '    def __init__(s, f, n): s.f, s.n = f, n\n'
                     '    def read(s, k=-1):\n'
                     '        k = s.n if k < 0 else min(k, s.n)\n'
                     '        d = s.f.read(k) if k > 0 else b""\n'
                     '        s.n -= len(d)\n'
                     '        return d\n'
                     'def short(self, m):\n'
                     '    f = _orig(self, m)\n'
                     '    return None if f is None else Short(f, m.size - 1)\n'
                     'tarfile.TarFile.extractfile = short\n'
                     'exec(compile(sys.stdin.read(), "<stdin>", "exec"))\' "$@"\n'
                     % (sys.executable, sys.executable),
}


@unittest.skipUnless(REAL_CURL, 'curl not found')
class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='orch-icx-test-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for d in ('home', 'scratch', 'fakebin', 'release/' + VERSION, 'bin'):
            os.makedirs(os.path.join(self.tmp, d))
        self.fakebin = os.path.join(self.tmp, 'fakebin')
        self.stub('uname'); self.stub('gh'); self.stub('curl')
        os.symlink(sys.executable, os.path.join(self.fakebin, 'python3'))
        self.dest = os.path.join(self.tmp, 'dest')
        self.served = os.path.join(self.tmp, 'release', VERSION, ARCHIVE)
        self.curl_log = os.path.join(self.tmp, 'curl.log')
        self.gh_log = os.path.join(self.tmp, 'gh.log')

    def stub(self, name, as_name=None):
        p = os.path.join(self.fakebin, as_name or name)
        if os.path.lexists(p):
            os.remove(p)
        with open(p, 'w') as f:
            f.write(STUBS[name])
        os.chmod(p, 0o755)

    def script(self, pin=None, pins=None, mutate=(), bundled=None):
        """A copy of the installer with every pin set to `pin` (or per target from `pins`, keyed by pin
        variable suffix), each BUNDLED pin set per target from `bundled` (when given), and each (old, new) in
        `mutate` applied once."""
        with open(SCRIPT) as f:
            text = f.read()
        text = re.sub(r'^(readonly SHA256_([A-Z0-9_]+)=)\S+$',
                      lambda m: m.group(1) + (pins[m.group(2)] if pins else pin), text, flags=re.M)
        if bundled is not None:
            text = re.sub(r'^(readonly BUNDLED_SHA256_([A-Z0-9_]+)=)\S+$',
                          lambda m: m.group(1) + bundled[m.group(2)], text, flags=re.M)
        for old, new in mutate:
            self.assertEqual(text.count(old), 1, 'mutation target not found once: %r' % old)
            text = text.replace(old, new)
        p = os.path.join(self.tmp, 'bin', 'orch-install-code-index.sh')
        with open(p, 'w') as f:
            f.write(text)
        os.chmod(p, 0o755)
        return p

    def serve(self, members, pin_from=None, mutate=()):
        """Write the served archive; return the pinned script (pinned to `pin_from`'s hash, else the archive's)."""
        make_tar(self.served, members)
        return self.script(sha256(pin_from or self.served), mutate=mutate)

    def run_it(self, script, *args, test_base=True, **env_extra):
        env = {'PATH': self.fakebin + ':' + SYS_PATH, 'HOME': os.path.join(self.tmp, 'home'),
               'TMPDIR': os.path.join(self.tmp, 'scratch'), 'CURL_LOG': self.curl_log, 'GH_LOG': self.gh_log}
        if test_base:
            env.update(ORCH_INSTALL_TEST='1', ORCH_INSTALL_TEST_BASE='file://' + os.path.join(self.tmp, 'release'))
        env.update(env_extra)
        argv = list(args) if args else ['--dest', self.dest]
        r = subprocess.run([script] + argv, env=env, capture_output=True, text=True, timeout=60)
        r.last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ''
        return r

    def curl_calls(self):
        if not os.path.exists(self.curl_log):
            return ''
        with open(self.curl_log) as f:
            return f.read()

    def assert_clean(self, installed=False, name='orch-index'):
        self.assertEqual(os.listdir(os.path.join(self.tmp, 'scratch')), [], 'temp dir left behind')
        if os.path.isdir(self.dest):
            left = sorted(os.listdir(self.dest))
            self.assertEqual(left, [name] if installed else [])
        else:
            self.assertFalse(installed)

    def good_members(self, binary=GOOD_BIN):
        return [(TOP, 'dir', None), (TOP + '/orch-index', 'file', binary), (TOP + '/LICENSE', 'file', b'MIT\n')]

    # -- the happy path ------------------------------------------------------------------------------

    def test_good_archive_installs(self):
        s = self.serve(self.good_members())
        r = self.run_it(s)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        final = os.path.join(self.dest, 'orch-index')
        self.assertEqual(r.last, 'installed %s %s' % (final, VERSION))
        for line in ('target ' + TARGET, 'checksum ok', 'archive ok', 'version ok 0.2.0',
                     'not attested (gh attestation verify failed)'):
            self.assertIn(line, r.stdout)
        with open(final, 'rb') as f:
            self.assertEqual(f.read(), GOOD_BIN)
        self.assertEqual(os.stat(final).st_mode & 0o777, 0o755)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'scratch', TOP)))  # nothing else unpacked
        self.assertEqual(self.curl_flag_problems(), [])
        self.assertIn('=file\n', self.curl_calls())
        self.assertIn('file://', self.curl_calls())
        self.assert_clean(installed=True)

    def curl_flag_problems(self):
        calls = self.curl_calls().splitlines()
        return [flag for flag in CURL_FLAGS if flag not in calls]

    def test_mutant_without_proto_redir_is_caught(self):
        s = self.serve(self.good_members(), mutate=[(' --proto-redir "$proto"', '')])
        r = self.run_it(s)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.curl_flag_problems(), ['--proto-redir'])

    def test_version_check_runs_in_dest(self):
        run_log = os.path.join(self.tmp, 'run.log')
        binary = b'#!/bin/sh\necho "$0" >> "$RUN_LOG"\necho "orch-index 0.2.0"\n'
        s = self.serve(self.good_members(binary=binary))
        r = self.run_it(s, RUN_LOG=run_log)
        self.assertEqual(r.returncode, 0, r.stdout)
        with open(run_log) as f:
            ran = f.read().split()
        self.assertEqual(len(ran), 1, ran)
        self.assertEqual(os.path.dirname(ran[0]), self.dest)
        self.assertTrue(os.path.basename(ran[0]).startswith('.orch-index.'), ran)
        self.assert_clean(installed=True)

    # -- each pin belongs to its own target -----------------------------------------------------------

    def serve_all_targets(self):
        """Serve one archive per target (each with its own top directory); return {pin var: its hash}."""
        pins = {}
        for target in PLATFORMS:
            top = 'orch-index-%s-%s' % (VERSION, target)
            path = os.path.join(self.tmp, 'release', VERSION, top + '.' + archive_ext(target))
            if target == WIN:
                make_zip(path, [(top + '/orch-index.exe', GOOD_BIN)])
            else:
                make_tar(path, [(top, 'dir', None), (top + '/orch-index', 'file', GOOD_BIN)])
            pins[pin_var(target)] = sha256(path)
        self.assertEqual(len(set(pins.values())), 5)
        return pins

    def installs_every_target(self, script):
        """True when every target installs from its own archive."""
        for target, (os_, arch) in PLATFORMS.items():
            dest = os.path.join(self.tmp, 'dest-' + target)
            r = self.run_it(script, '--dest', dest, FAKE_OS=os_, FAKE_ARCH=arch)
            if (r.returncode, r.last) != (0, 'installed %s %s' % (os.path.join(dest, exe_name(target)), VERSION)):
                return False
        return True

    def test_each_pin_tied_to_its_target(self):
        pins = self.serve_all_targets()
        self.assertTrue(self.installs_every_target(self.script(pins=pins)))

    def test_swapped_pins_fail_closed(self):
        pins = self.serve_all_targets()
        pins['AARCH64_APPLE_DARWIN'], pins['X86_64_APPLE_DARWIN'] = pins['X86_64_APPLE_DARWIN'], pins['AARCH64_APPLE_DARWIN']
        r = self.run_it(self.script(pins=pins), FAKE_OS='Darwin', FAKE_ARCH='arm64')
        self.assertEqual((r.returncode, r.last), (1, 'failed: checksum mismatch'))
        self.assert_clean()

    def test_mutant_wrong_pin_variable_is_caught(self):
        pins = self.serve_all_targets()
        s = self.script(pins=pins, mutate=[('pin=$SHA256_X86_64_APPLE_DARWIN', 'pin=$SHA256_AARCH64_APPLE_DARWIN')])
        self.assertFalse(self.installs_every_target(s))

    # -- opt-out ---------------------------------------------------------------------------------------

    def test_autoinstall_0_disables_without_network(self):
        s = self.serve(self.good_members())
        r = self.run_it(s, FAKE_CURL_BLOCK='1', ORCH_INDEX_AUTOINSTALL='0')
        self.assertEqual((r.returncode, r.stdout), (3, 'disabled by ORCH_INDEX_AUTOINSTALL\n'))
        self.assertEqual(self.curl_calls(), '')
        self.assertFalse(os.path.exists(self.dest))
        self.assert_clean()

    def test_autoinstall_other_values_install(self):
        s = self.serve(self.good_members())
        for value in ('1', '', 'false', 'no', '00'):
            dest = os.path.join(self.tmp, 'dest-%r' % value)
            r = self.run_it(s, '--dest', dest, ORCH_INDEX_AUTOINSTALL=value)
            self.assertEqual((r.returncode, r.last), (0, 'installed %s %s' % (os.path.join(dest, 'orch-index'), VERSION)),
                             value)

    def test_attested_when_gh_verifies(self):
        s = self.serve(self.good_members())
        r = self.run_it(s, FAKE_GH_RC='0')
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('\nattested\n', r.stdout)
        with open(self.gh_log) as f:
            self.assertIn('attestation verify', f.read())

    def test_gh_missing_reports_not_attested(self):
        if shutil.which('gh', path=SYS_PATH):
            self.skipTest('gh is installed in a system dir')
        os.remove(os.path.join(self.fakebin, 'gh'))
        s = self.serve(self.good_members())
        r = self.run_it(s)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('not attested (gh not found)', r.stdout)

    def test_creates_missing_dest(self):
        s = self.serve(self.good_members())
        dest = os.path.join(self.tmp, 'a', 'b')
        r = self.run_it(s, '--dest', dest)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(os.stat(dest).st_mode & 0o777, 0o755)

    # -- verification failures install nothing --------------------------------------------------------

    def test_hash_mismatch_installs_nothing(self):
        other = os.path.join(self.tmp, 'other.tar.gz')
        make_tar(other, self.good_members(binary=b'#!/bin/sh\necho other 0.2.0\n'))
        s = self.serve(self.good_members(), pin_from=other)
        r = self.run_it(s)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.last, 'failed: checksum mismatch')
        self.assertNotIn('archive ok', r.stdout)
        self.assert_clean()

    def test_truncated_download_fails_closed(self):
        full = os.path.join(self.tmp, 'full.tar.gz')
        make_tar(full, self.good_members())
        with open(full, 'rb') as f:
            data = f.read()
        s = self.script(sha256(full))
        with open(self.served, 'wb') as f:
            f.write(data[: len(data) // 2])
        r = self.run_it(s)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.last, 'failed: checksum mismatch')
        self.assert_clean()

    SIZE_CAP = ('readonly MAX_BYTES=67108864 ', 'readonly MAX_BYTES=64 ')
    SIZE_CHECK = ('[ "$size" -le "$MAX_BYTES" ] || fail "download larger than $MAX_BYTES bytes"\n', '')

    def refuses_oversize(self, script):
        r = self.run_it(script, FAKE_CURL_NOMAX='1')
        return (r.returncode, r.last) == (1, 'failed: download larger than 64 bytes') and not os.path.exists(self.dest)

    def test_oversize_download_refused_after_transfer(self):
        self.assertTrue(self.refuses_oversize(self.serve(self.good_members(), mutate=[self.SIZE_CAP])))
        self.assert_clean()

    def test_mutant_without_size_check_is_caught(self):
        self.assertFalse(self.refuses_oversize(self.serve(self.good_members(), mutate=[self.SIZE_CAP, self.SIZE_CHECK])))

    SHORT_CHECK = ('if total != found.size:\n    die("short binary member")\n', '')

    def refuses_short_member(self, script):
        self.stub('python3-short', as_name='python3')
        r = self.run_it(script)
        return (r.returncode, r.last) == (1, 'failed: short binary member') and not os.path.exists(self.dest)

    def test_short_member_refused(self):
        self.assertTrue(self.refuses_short_member(self.serve(self.good_members())))
        self.assert_clean()

    def test_mutant_without_short_member_check_is_caught(self):
        self.assertFalse(self.refuses_short_member(self.serve(self.good_members(), mutate=[self.SHORT_CHECK])))

    def test_missing_download_fails(self):
        s = self.script('0' * 64)
        r = self.run_it(s)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.last, 'failed: download failed')
        self.assert_clean()

    def hostile(self, members, why):
        s = self.serve(members)
        r = self.run_it(s)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(r.last.startswith('failed: archive member rejected:'), r.last)
        self.assertIn('(%s)' % why, r.last)
        self.assert_clean()

    def test_rejects_parent_dir_member(self):
        self.hostile(self.good_members() + [(TOP + '/../evil', 'file', b'x')], 'parent directory')

    def test_rejects_bare_parent_member(self):
        self.hostile(self.good_members() + [('../evil', 'file', b'x')], 'parent directory')

    def test_rejects_absolute_member(self):
        self.hostile(self.good_members() + [('/tmp/evil', 'file', b'x')], 'absolute path')

    def test_rejects_symlink_member(self):
        self.hostile([(TOP, 'dir', None), (TOP + '/orch-index', 'sym', '/bin/sh')], 'symlink')

    def test_rejects_hardlink_member(self):
        self.hostile(self.good_members() + [(TOP + '/README', 'link', TOP + '/orch-index')], 'hardlink')

    def test_rejects_second_binary(self):
        self.hostile(self.good_members() + [(TOP + '/orch-index-helper', 'file', GOOD_BIN)], 'unexpected member')

    def test_rejects_duplicate_binary(self):
        self.hostile(self.good_members() + [(TOP + '/orch-index', 'file', GOOD_BIN)], 'duplicate binary')

    def test_rejects_archive_without_binary(self):
        s = self.serve([(TOP, 'dir', None), (TOP + '/LICENSE', 'file', b'MIT\n')])
        r = self.run_it(s)
        self.assertEqual((r.returncode, r.last), (1, 'failed: binary missing from archive'))
        self.assert_clean()

    def test_wrong_version_binary_refused(self):
        s = self.serve(self.good_members(binary=b'#!/bin/sh\necho "orch-index 0.1.0"\n'))
        r = self.run_it(s)
        self.assertEqual((r.returncode, r.last), (1, 'failed: binary reports a version other than 0.2.0'))
        self.assert_clean()

    # -- no-network paths ------------------------------------------------------------------------------

    def test_already_installed_untouched_no_network(self):
        os.makedirs(self.dest)
        final = os.path.join(self.dest, 'orch-index')
        with open(final, 'w') as f:
            f.write('keep')
        s = self.serve(self.good_members())
        r = self.run_it(s, FAKE_CURL_BLOCK='1')
        self.assertEqual((r.returncode, r.last), (0, 'already installed ' + final))
        with open(final) as f:
            self.assertEqual(f.read(), 'keep')
        self.assertEqual(self.curl_calls(), '')
        self.assertFalse(os.path.exists(self.gh_log))

    def test_dry_run_no_network_no_writes(self):
        s = self.serve(self.good_members())
        r = self.run_it(s, '--dest', self.dest, '--dry-run', FAKE_CURL_BLOCK='1')
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(r.last, 'dry-run: would install %s %s' % (os.path.join(self.dest, 'orch-index'), VERSION))
        self.assertEqual(self.curl_calls(), '')
        self.assertFalse(os.path.exists(self.dest))
        self.assert_clean()

    def test_print_target_no_network_no_writes(self):
        s = self.serve(self.good_members())
        cases = {('Darwin', 'arm64'): 'aarch64-apple-darwin', ('Darwin', 'x86_64'): 'x86_64-apple-darwin',
                 ('Linux', 'x86_64'): 'x86_64-unknown-linux-musl', ('Linux', 'aarch64'): 'aarch64-unknown-linux-musl',
                 ('MINGW64_NT-10.0', 'x86_64'): WIN, ('MINGW64_NT-10.0-26100', 'x86_64'): WIN,
                 ('MSYS_NT-10.0', 'x86_64'): WIN, ('CYGWIN_NT-10.0', 'x86_64'): WIN}
        for (os_, arch), want in cases.items():
            r = self.run_it(s, '--dest', self.dest, '--print-target', FAKE_CURL_BLOCK='1', FAKE_OS=os_, FAKE_ARCH=arch)
            self.assertEqual((r.returncode, r.stdout), (0, want + '\n'))
        self.assertEqual(self.curl_calls(), '')
        self.assertFalse(os.path.exists(self.dest))
        self.assert_clean()

    def test_unsupported_platform_exits_3(self):
        s = self.serve(self.good_members())
        for os_, arch in (('MINGW64_NT-10.0', 'aarch64'), ('Linux', 'riscv64'), ('FreeBSD', 'amd64')):
            r = self.run_it(s, FAKE_CURL_BLOCK='1', FAKE_OS=os_, FAKE_ARCH=arch)
            self.assertEqual((r.returncode, r.last), (3, 'unsupported platform %s/%s' % (os_, arch)))
            self.assertIn('https://github.com/ijat/orch-index#install', r.stdout)
        self.assertEqual(self.curl_calls(), '')
        self.assert_clean()

    def test_refuses_root(self):
        self.stub('id')
        s = self.serve(self.good_members())
        r = self.run_it(s, FAKE_CURL_BLOCK='1')
        self.assertEqual((r.returncode, r.last), (1, 'failed: refusing to run as root'))
        self.assertEqual(self.curl_calls(), '')
        self.assert_clean()

    def test_pending_pin_fails_without_network(self):
        r = self.run_it(self.script('PIN_PENDING'), FAKE_CURL_BLOCK='1')
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.last.startswith('failed: no pinned checksum for ' + TARGET), r.last)
        self.assertEqual(self.curl_calls(), '')

    def test_bad_usage_exits_2(self):
        s = self.script('0' * 64)
        for argv in (['--bogus'], ['--dest'], ['--dest', ''], ['--url', 'https://example.com']):
            r = self.run_it(s, *argv, FAKE_CURL_BLOCK='1')
            self.assertEqual(r.returncode, 2, argv)
        self.assertEqual(self.curl_calls(), '')

    # -- the environment cannot redirect the download -------------------------------------------------

    def test_env_cannot_change_url_or_version(self):
        s = self.script('0' * 64)
        r = self.run_it(s, test_base=False, FAKE_CURL_BLOCK='1', ORCH_INSTALL_TEST_BASE='file:///nowhere',
                        ORCH_INDEX_VERSION='v9.9.9', RELEASE_BASE='https://example.com')
        self.assertEqual((r.returncode, r.last), (1, 'failed: download failed'))
        calls = self.curl_calls().splitlines()
        self.assertIn('https://github.com/ijat/orch-index/releases/download/%s/%s' % (VERSION, ARCHIVE), calls)
        self.assertIn('=https', calls)
        self.assertNotIn('file:///nowhere', self.curl_calls())
        self.assert_clean()

    def test_test_base_must_be_file_url(self):
        s = self.script('0' * 64)
        r = self.run_it(s, test_base=False, FAKE_CURL_BLOCK='1', ORCH_INSTALL_TEST='1',
                        ORCH_INSTALL_TEST_BASE='https://example.com/x')
        self.assertEqual((r.returncode, r.last), (1, 'failed: test base must be a file:/// URL'))
        self.assertEqual(self.curl_calls(), '')

    # -- Windows (Git for Windows bash): a zip holding orch-index.exe, installed as orch-index.exe ------

    WIN_TOP = 'orch-index-%s-%s' % (VERSION, WIN)
    WIN_ARCHIVE = WIN_TOP + '.zip'

    def serve_zip(self, members, mutate=()):
        path = os.path.join(self.tmp, 'release', VERSION, self.WIN_ARCHIVE)
        make_zip(path, members)
        return self.script(sha256(path), mutate=mutate)

    def win_members(self, binary=GOOD_BIN):
        return [(self.WIN_TOP + '/LICENSE', b'MIT\n'), (self.WIN_TOP + '/orch-index.exe', binary),
                (self.WIN_TOP + '/README.md', b'x\n')]

    def run_win(self, script, *args, **env):
        return self.run_it(script, *args, FAKE_OS='MINGW64_NT-10.0-26100', FAKE_ARCH='x86_64', **env)

    NO_UNZIP = [('unzip_bin=$(command -v unzip || true)', 'unzip_bin=')]   # forces the python3 fallback

    def check_windows_installs(self, mutate):
        s = self.serve_zip(self.win_members(), mutate=mutate)
        r = self.run_win(s)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.last, 'installed %s %s' % (os.path.join(self.dest, 'orch-index.exe'), VERSION))
        for line in ('target ' + WIN, 'checksum ok', 'archive ok', 'version ok 0.2.0'):
            self.assertIn(line, r.stdout)
        self.assertIn(self.WIN_ARCHIVE, self.curl_calls())
        with open(os.path.join(self.dest, 'orch-index.exe'), 'rb') as f:
            self.assertEqual(f.read(), GOOD_BIN)
        self.assert_clean(installed=True, name='orch-index.exe')

    def test_windows_zip_installs_with_unzip(self):
        if not shutil.which('unzip', path=SYS_PATH):
            self.skipTest('unzip not found')
        self.check_windows_installs(())

    def test_windows_zip_installs_with_python_fallback(self):
        self.check_windows_installs(self.NO_UNZIP)

    def test_windows_zip_hostile_or_wrong_archives_refused(self):
        top = self.WIN_TOP
        cases = {
            'no binary': [(top + '/LICENSE', b'MIT\n')],
            'binary under another name': [(top + '/orch-index', GOOD_BIN)],
            'binary under another dir': [('evil/orch-index.exe', GOOD_BIN)],
            'duplicate binary': [(top + '/orch-index.exe', GOOD_BIN), (top + '/orch-index.exe', GOOD_BIN)],
        }
        for mutate in ((), self.NO_UNZIP):
            for name, members in cases.items():
                with self.subTest(case=name, python=bool(mutate)):
                    s = self.serve_zip(members, mutate=mutate)
                    r = self.run_win(s)
                    self.assertEqual(r.returncode, 1, r.stdout)
                    self.assertTrue(r.last.startswith('failed: binary missing from archive'), r.last)
                    self.assert_clean()

    def test_windows_zip_hash_mismatch_installs_nothing(self):
        path = os.path.join(self.tmp, 'release', VERSION, self.WIN_ARCHIVE)
        make_zip(path, self.win_members())
        r = self.run_win(self.script('0' * 64))
        self.assertEqual((r.returncode, r.last), (1, 'failed: checksum mismatch'))
        self.assert_clean()

    def test_windows_zip_wrong_version_binary_refused(self):
        s = self.serve_zip(self.win_members(b'#!/bin/sh\necho "orch-index 9.9.9"\n'))
        r = self.run_win(s)
        self.assertEqual((r.returncode, r.last), (1, 'failed: binary reports a version other than 0.2.0'))
        self.assert_clean()

    def test_windows_zip_without_unzip_or_python_fails_with_manual_url(self):
        s = self.serve_zip(self.win_members(), mutate=self.NO_UNZIP + [('py=$(command -v python3 || true)', 'py=')])
        r = self.run_win(s, FAKE_CURL_BLOCK='1')
        self.assertEqual(r.returncode, 1)
        self.assertIn('neither unzip nor python3 found', r.last)
        self.assertIn('https://github.com/ijat/orch-index#install', r.last)
        self.assertEqual(self.curl_calls(), '')

    def test_windows_dry_run_names_the_zip(self):
        r = self.run_win(self.script('0' * 64), '--dest', self.dest, '--dry-run', FAKE_CURL_BLOCK='1')
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('/%s/%s' % (VERSION, self.WIN_ARCHIVE), r.stdout)
        self.assertEqual(r.last, 'dry-run: would install %s %s' % (os.path.join(self.dest, 'orch-index.exe'), VERSION))

    def test_windows_already_installed_exe_untouched(self):
        os.makedirs(self.dest)
        final = os.path.join(self.dest, 'orch-index.exe')
        with open(final, 'w') as f:
            f.write('mine')
        r = self.run_win(self.script('0' * 64), FAKE_CURL_BLOCK='1')
        self.assertEqual((r.returncode, r.last), (0, 'already installed ' + final))
        self.assertEqual(self.curl_calls(), '')

    # -- bundled binaries (bin/vendor/orch-index-<target>[.exe]) beat the download ----------------------

    def bundled_script(self, tamper=None):
        """A copy of the installer with a vendor/ beside it: one fake binary per target, each BUNDLED pin set to
        its own binary's hash (`tamper`: a target whose binary then differs from its pin)."""
        script = self.script('0' * 64)
        vendor = os.path.join(os.path.dirname(script), 'vendor')
        os.makedirs(vendor, exist_ok=True)
        pins = {}
        for target in PLATFORMS:
            data = b'#!/bin/sh\necho "orch-index 0.2.0"\n# %s\n' % target.encode()
            path = os.path.join(vendor, 'orch-index-' + target + ('.exe' if target == WIN else ''))
            with open(path, 'wb') as f:
                f.write(data)
            os.chmod(path, 0o755)
            pins[pin_var(target)] = sha256(path)
            if target == tamper:
                with open(path, 'ab') as f:
                    f.write(b'# tampered\n')
        return self.script('0' * 64, bundled=pins)

    def test_bundled_binary_installs_for_every_target_without_network(self):
        s = self.bundled_script()
        for target, (os_, arch) in PLATFORMS.items():
            with self.subTest(target=target):
                dest = os.path.join(self.tmp, 'dest-' + target)
                r = self.run_it(s, '--dest', dest, FAKE_OS=os_, FAKE_ARCH=arch, FAKE_CURL_BLOCK='1')
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(r.last, 'installed %s %s' % (os.path.join(dest, exe_name(target)), VERSION))
                self.assertTrue(any(l.startswith('bundled ') and l.endswith('(checksum ok)') for l in r.stdout.splitlines()))
                with open(os.path.join(dest, exe_name(target)), 'rb') as f:
                    self.assertIn(target.encode(), f.read())     # this target's own binary, not another's
        self.assertEqual(self.curl_calls(), '')

    def test_bundled_binary_with_wrong_hash_refused(self):
        for target, (os_, arch) in PLATFORMS.items():
            with self.subTest(target=target):
                s = self.bundled_script(tamper=target)
                dest = os.path.join(self.tmp, 'dest-' + target)
                r = self.run_it(s, '--dest', dest, FAKE_OS=os_, FAKE_ARCH=arch, FAKE_CURL_BLOCK='1')
                self.assertEqual((r.returncode, r.last), (1, 'failed: bundled binary checksum mismatch'))
                self.assertEqual(os.listdir(dest) if os.path.isdir(dest) else [], [])
                self.assertEqual(os.listdir(os.path.join(self.tmp, 'scratch')), [])

    def test_mutant_wrong_bundled_pin_variable_is_caught(self):
        s = self.bundled_script()
        with open(s) as f:
            text = f.read()
        old = 'bundled_pin=$BUNDLED_SHA256_X86_64_PC_WINDOWS_MSVC'
        self.assertEqual(text.count(old), 1)
        with open(s, 'w') as f:
            f.write(text.replace(old, 'bundled_pin=$BUNDLED_SHA256_X86_64_APPLE_DARWIN'))
        r = self.run_it(s, '--dest', self.dest, FAKE_OS='MINGW64_NT-10.0', FAKE_ARCH='x86_64', FAKE_CURL_BLOCK='1')
        self.assertEqual((r.returncode, r.last), (1, 'failed: bundled binary checksum mismatch'))

    def test_a_symlinked_bundled_binary_is_not_used(self):
        s = self.bundled_script()
        cand = os.path.join(os.path.dirname(s), 'vendor', 'orch-index-' + TARGET)
        real = cand + '.real'
        os.rename(cand, real)
        os.symlink(real, cand)
        r = self.run_it(s, '--dest', self.dest, FAKE_CURL_BLOCK='1')
        self.assertEqual((r.returncode, r.last), (1, 'failed: download failed'))   # fell through to the download

    # -- the shipped bundle -------------------------------------------------------------------------

    def test_shipped_vendor_binaries_match_their_bundled_pins(self):
        with open(SCRIPT) as f:
            text = f.read()
        pins = dict(re.findall(r'(?m)^readonly BUNDLED_SHA256_([A-Z0-9_]+)=([0-9a-f]{64})$', text))
        self.assertEqual(sorted(pins), sorted(TARGETS))
        for target in PLATFORMS:
            with self.subTest(target=target):
                path = os.path.join(VENDOR, 'orch-index-' + target + ('.exe' if target == WIN else ''))
                self.assertTrue(os.path.isfile(path) and not os.path.islink(path), path)
                self.assertEqual(sha256(path), pins[pin_var(target)])
                self.assertLess(os.path.getsize(path), 50 * 1000 * 1000, 'GitHub warns above 50 MB per file')
                if target != WIN:
                    self.assertTrue(os.access(path, os.X_OK), path)

    # -- the shipped script on the real release archives ----------------------------------------------

    @unittest.skipUnless(os.environ.get('ORCH_INSTALL_REAL_ARCHIVES_DIR'),
                         'set ORCH_INSTALL_REAL_ARCHIVES_DIR to a dir holding the five real archives')
    def test_real_v020_archives(self):
        """The shipped script (real pins, no rewrite) on each real archive, via the file:/// test hook and a
        fake `uname` that selects the target. Every target must pass the pin, the archive whitelist and
        reach the --version check. Only a target this host can execute is also required to install;
        any other must install or fail exactly at `binary does not run` with nothing left behind."""
        src = os.path.realpath(os.environ['ORCH_INSTALL_REAL_ARCHIVES_DIR'])
        # The shipped script, byte for byte, but beside no vendor/ so it downloads (the bundle is checked below).
        script = os.path.join(self.tmp, 'bin', 'orch-install-code-index.sh')
        shutil.copyfile(SCRIPT, script)
        os.chmod(script, 0o755)
        host = (os.uname().sysname, os.uname().machine.replace('arm64', 'aarch64'))
        covered = {}
        for target, (os_name, arch) in PLATFORMS.items():
            with self.subTest(target=target):
                name = 'orch-index-%s-%s.%s' % (VERSION, target, archive_ext(target))
                self.assertTrue(os.path.isfile(os.path.join(src, name)), name)
                shutil.copyfile(os.path.join(src, name), os.path.join(self.tmp, 'release', VERSION, name))
                dest = os.path.join(self.tmp, 'dest-' + target)
                r = self.run_it(script, '--dest', dest, FAKE_OS=os_name, FAKE_ARCH=arch)
                lines = r.stdout.splitlines()
                for step in ('target ' + target, 'checksum ok', 'archive ok'):
                    self.assertIn(step, lines, r.stdout)
                self.assertEqual(os.listdir(os.path.join(self.tmp, 'scratch')), [], 'temp dir left behind')
                runs_here = (os_name, arch.replace('arm64', 'aarch64')) == host
                if r.returncode == 0:
                    self.assertEqual(r.last, 'installed %s %s' % (os.path.join(dest, exe_name(target)), VERSION))
                    self.assertIn('version ok 0.2.0', lines)
                    self.assertEqual(os.listdir(dest), [exe_name(target)])
                    covered[target] = 'installed, version ok (executed here)'
                else:
                    self.assertFalse(runs_here, 'host-native target must install: %s' % r.stdout)
                    self.assertEqual((r.returncode, r.last), (1, 'failed: binary does not run'))
                    self.assertEqual(os.listdir(dest), [])
                    covered[target] = 'pin + archive whitelist ok; stopped at --version (cannot execute here)'
        for target, what in sorted(covered.items()):
            print('real archive %s: %s' % (target, what), file=sys.stderr)

    @unittest.skipUnless(os.environ.get('ORCH_INSTALL_REAL_ARCHIVES_DIR'),
                         'set ORCH_INSTALL_REAL_ARCHIVES_DIR to a dir holding the five real archives')
    def test_vendor_binaries_are_the_release_binaries(self):
        """Each bundled file is byte-identical to the binary inside the real v0.2.0 archive of its target."""
        src = os.path.realpath(os.environ['ORCH_INSTALL_REAL_ARCHIVES_DIR'])
        for target in PLATFORMS:
            with self.subTest(target=target):
                top = 'orch-index-%s-%s' % (VERSION, target)
                path = os.path.join(src, top + '.' + archive_ext(target))
                if target == WIN:
                    with zipfile.ZipFile(path) as zf:
                        want = zf.read(top + '/orch-index.exe')
                else:
                    with tarfile.open(path) as tf:
                        want = tf.extractfile(top + '/orch-index').read()
                with open(os.path.join(VENDOR, 'orch-index-' + target + ('.exe' if target == WIN else '')), 'rb') as f:
                    self.assertEqual(f.read() == want, True)


class LauncherTest(unittest.TestCase):
    """bin/orch-index, the launcher: target mapping and lookup order, per platform (fake `uname` via a PATH stub)."""
    LAUNCHER = os.path.join(BIN, 'orch-index')

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='orch-launcher-test-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for d in ('bin/vendor', 'fakebin', 'data/bin', 'pathbin'):
            os.makedirs(os.path.join(self.tmp, d))
        shutil.copyfile(self.LAUNCHER, os.path.join(self.tmp, 'bin', 'orch-index'))
        os.chmod(os.path.join(self.tmp, 'bin', 'orch-index'), 0o755)
        with open(os.path.join(self.tmp, 'fakebin', 'uname'), 'w') as f:
            f.write(STUBS['uname'])
        os.chmod(os.path.join(self.tmp, 'fakebin', 'uname'), 0o755)

    def fake(self, *parts):
        path = os.path.join(self.tmp, *parts)
        with open(path, 'w') as f:
            f.write('#!/bin/sh\necho "%s $*"\n' % '/'.join(parts))
        os.chmod(path, 0o755)

    def run_it(self, os_, arch, *args, **env):
        e = {'PATH': ':'.join([os.path.join(self.tmp, 'fakebin'), os.path.join(self.tmp, 'pathbin'), SYS_PATH]),
             'HOME': os.path.join(self.tmp, 'home'), 'ORCH_DATA': os.path.join(self.tmp, 'data'),
             'FAKE_OS': os_, 'FAKE_ARCH': arch}
        e.update(env)
        return subprocess.run([os.path.join(self.tmp, 'bin', 'orch-index')] + list(args), env=e,
                              capture_output=True, text=True, timeout=30)

    def test_each_platform_runs_its_own_bundled_binary(self):
        for target, (os_, arch) in PLATFORMS.items():
            self.fake('bin', 'vendor', 'orch-index-' + target + ('.exe' if target == WIN else ''))
        for target, (os_, arch) in PLATFORMS.items():
            with self.subTest(target=target):
                r = self.run_it(os_, arch, 'stats')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout.strip(), 'bin/vendor/orch-index-%s%s stats' % (target, '.exe' if target == WIN else ''))

    def test_windows_aliases(self):
        self.fake('bin', 'vendor', 'orch-index-' + WIN + '.exe')
        for os_ in ('MINGW64_NT-10.0-26100', 'MINGW32_NT-10.0', 'MSYS_NT-10.0', 'CYGWIN_NT-10.0'):
            r = self.run_it(os_, 'x86_64')
            self.assertEqual(r.returncode, 0, (os_, r.stderr))

    def test_windows_never_picks_a_non_exe_name(self):
        for where in (('bin', 'vendor', 'orch-index-' + WIN), ('data', 'bin', 'orch-index'), ('pathbin', 'orch-index')):
            self.fake(*where)
        r = self.run_it('MINGW64_NT-10.0', 'x86_64')
        self.assertEqual((r.returncode, r.stdout), (127, ''))
        self.assertIn('no binary for x86_64-pc-windows-msvc', r.stderr)

    def test_windows_falls_back_to_data_dir_then_path_exe(self):
        self.fake('pathbin', 'orch-index.exe')
        r = self.run_it('MINGW64_NT-10.0', 'x86_64')
        self.assertEqual(r.stdout.strip(), 'pathbin/orch-index.exe')
        self.fake('data', 'bin', 'orch-index.exe')
        r = self.run_it('MINGW64_NT-10.0', 'x86_64')
        self.assertEqual(r.stdout.strip(), 'data/bin/orch-index.exe')

    def test_unix_lookup_order_unchanged(self):
        self.fake('pathbin', 'orch-index')
        self.assertEqual(self.run_it('Linux', 'x86_64').stdout.strip(), 'pathbin/orch-index')
        self.fake('data', 'bin', 'orch-index')
        self.assertEqual(self.run_it('Linux', 'x86_64').stdout.strip(), 'data/bin/orch-index')
        self.fake('bin', 'vendor', 'orch-index-x86_64-unknown-linux-musl')
        self.assertEqual(self.run_it('Linux', 'x86_64').stdout.strip(), 'bin/vendor/orch-index-x86_64-unknown-linux-musl')
        other = os.path.join(self.tmp, 'other')
        self.fake('other')
        self.assertEqual(self.run_it('Linux', 'x86_64', ORCH_INDEX_REAL=other).stdout.strip(), 'other')

    def test_the_shipped_launcher_runs_the_bundled_binary_here(self):
        if (os.uname().sysname, os.uname().machine) != ('Darwin', 'arm64'):
            self.skipTest('this host cannot run the bundled arm64 binary')
        r = subprocess.run([self.LAUNCHER, '--version'], capture_output=True, text=True, timeout=30,
                           env={'PATH': SYS_PATH, 'HOME': os.path.join(self.tmp, 'home')})
        self.assertEqual((r.returncode, r.stdout.strip()), (0, 'orch-index 0.2.0'))


class PinsTest(unittest.TestCase):
    def test_pins_are_real(self):
        """The shipped pin block has a version and one 64-hex hash per target, no placeholder."""
        with open(SCRIPT) as f:
            text = f.read()
        m = re.search(r'^# --- pins \(begin\) ---\n(.*?)^# --- pins \(end\) ---$', text, re.M | re.S)
        self.assertIsNotNone(m, 'pin block not found')
        block = m.group(1)
        self.assertNotIn('PIN_PENDING', block)
        self.assertRegex(block, r'(?m)^readonly ORCH_INDEX_VERSION=v[0-9]+\.[0-9]+\.[0-9]+$')
        pins = dict(re.findall(r'(?m)^readonly SHA256_([A-Z0-9_]+)=(\S+)$', block))
        self.assertEqual(sorted(pins), sorted(TARGETS))
        for t, h in pins.items():
            self.assertRegex(h, r'\A[0-9a-f]{64}\Z', t)

    def test_pin_block_shape(self):
        """The pin block has the version line and one line per unix target (placeholders allowed)."""
        with open(SCRIPT) as f:
            text = f.read()
        self.assertIn('readonly ORCH_INDEX_VERSION=%s\n' % VERSION, text)
        for t in TARGETS:
            self.assertRegex(text, r'(?m)^readonly SHA256_%s=(PIN_PENDING|[0-9a-f]{64})$' % t)


if __name__ == '__main__':
    unittest.main()
