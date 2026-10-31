"""bin/orch-install-code-index.sh: the pinned, hash-verified orch-index installer.

No real network: every run gets a throwaway HOME and TMPDIR and a PATH whose first entry holds stubs
(`uname`, `gh`, `curl`; `id` for the root case). The `curl` stub logs its arguments and either runs the
real curl (file:// URLs only, via ORCH_INSTALL_TEST_BASE) or refuses. Runs use a copy of the script whose
pin block is rewritten to the test archive's hash; `test_pins_are_real` checks the shipped pins and is
expected to fail until the v0.2.0 release hashes are filled in. The `test_mutant_*` tests remove one guard
from a copy of the script and check that the guard's own test then fails."""
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

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(BIN, 'orch-install-code-index.sh')
SYS_PATH = '/usr/bin:/bin:/usr/sbin:/sbin'
REAL_CURL = shutil.which('curl', path=SYS_PATH)
VERSION = 'v0.2.0'
TARGET = 'aarch64-apple-darwin'
TOP = 'orch-index-%s-%s' % (VERSION, TARGET)
ARCHIVE = TOP + '.tar.gz'
GOOD_BIN = b'#!/bin/sh\necho "orch-index 0.2.0"\n'
TARGETS = ('AARCH64_APPLE_DARWIN', 'X86_64_APPLE_DARWIN', 'X86_64_UNKNOWN_LINUX_MUSL',
           'AARCH64_UNKNOWN_LINUX_MUSL')
PLATFORMS = {'aarch64-apple-darwin': ('Darwin', 'arm64'), 'x86_64-apple-darwin': ('Darwin', 'x86_64'),
             'x86_64-unknown-linux-musl': ('Linux', 'x86_64'), 'aarch64-unknown-linux-musl': ('Linux', 'aarch64')}
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

    def script(self, pin=None, pins=None, mutate=()):
        """A copy of the installer with every pin set to `pin` (or per target from `pins`, keyed by pin
        variable suffix), and each (old, new) in `mutate` applied once."""
        with open(SCRIPT) as f:
            text = f.read()
        text = re.sub(r'^(readonly SHA256_([A-Z0-9_]+)=)\S+$',
                      lambda m: m.group(1) + (pins[m.group(2)] if pins else pin), text, flags=re.M)
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

    def assert_clean(self, installed=False):
        self.assertEqual(os.listdir(os.path.join(self.tmp, 'scratch')), [], 'temp dir left behind')
        if os.path.isdir(self.dest):
            left = sorted(os.listdir(self.dest))
            self.assertEqual(left, ['orch-index'] if installed else [])
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
            path = os.path.join(self.tmp, 'release', VERSION, top + '.tar.gz')
            make_tar(path, [(top, 'dir', None), (top + '/orch-index', 'file', GOOD_BIN)])
            pins[pin_var(target)] = sha256(path)
        self.assertEqual(len(set(pins.values())), 4)
        return pins

    def installs_every_target(self, script):
        """True when every target installs from its own archive."""
        for target, (os_, arch) in PLATFORMS.items():
            dest = os.path.join(self.tmp, 'dest-' + target)
            r = self.run_it(script, '--dest', dest, FAKE_OS=os_, FAKE_ARCH=arch)
            if (r.returncode, r.last) != (0, 'installed %s %s' % (os.path.join(dest, 'orch-index'), VERSION)):
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
                 ('Linux', 'x86_64'): 'x86_64-unknown-linux-musl', ('Linux', 'aarch64'): 'aarch64-unknown-linux-musl'}
        for (os_, arch), want in cases.items():
            r = self.run_it(s, '--dest', self.dest, '--print-target', FAKE_CURL_BLOCK='1', FAKE_OS=os_, FAKE_ARCH=arch)
            self.assertEqual((r.returncode, r.stdout), (0, want + '\n'))
        self.assertEqual(self.curl_calls(), '')
        self.assertFalse(os.path.exists(self.dest))
        self.assert_clean()

    def test_unsupported_platform_exits_3(self):
        s = self.serve(self.good_members())
        for os_, arch in (('MINGW64_NT-10.0', 'x86_64'), ('Linux', 'riscv64'), ('FreeBSD', 'amd64')):
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


class PinsTest(unittest.TestCase):
    def test_pins_are_real(self):
        """Expected to FAIL until orch-index v0.2.0 is published and its hashes are pinned."""
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
