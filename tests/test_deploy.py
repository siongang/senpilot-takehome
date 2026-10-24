"""Exercise release ordering and recovery without Docker, SSH, or real data."""

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


RELEASE_SCRIPT = Path(__file__).resolve().parents[1] / 'deploy' / 'release.sh'
REVISION = 'a' * 40


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('flock') and RELEASE_SCRIPT.is_file(),
                     'Release script requires Linux; runs in GitHub Actions')
class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.base = self.root / 'app'
        self.base.mkdir()
        (self.base / '.env').write_text('TEST_ONLY=true\n')
        self.archive = self.root / 'release.tar.gz'
        self.log = self.root / 'commands.jsonl'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        fake_docker = self.bin / 'docker'
        fake_docker.write_text('#!' + sys.executable + '\n' + '''
import json, os, sys
args = sys.argv[1:]
with open(os.environ['COMMAND_LOG'], 'a') as log:
    log.write(json.dumps({'args': args, 'image': os.environ.get('SENPILOT_IMAGE')}) + '\\n')
if args[0] == 'inspect':
    print('senpilot-agent:previous')
if args[0] == 'build' and os.environ.get('FAIL_BUILD'):
    sys.exit(1)
if args[0] == 'run' and 'alpine:3.22' in args:
    print('fake-runtime-backup')
if (args[0] == 'compose' and 'up' in args and os.environ.get('FAIL_START')
        and os.environ.get('SENPILOT_IMAGE') != 'senpilot-agent:previous'):
    sys.exit(1)
''')
        fake_docker.chmod(0o755)
        fake_curl = self.bin / 'curl'
        fake_curl.write_text('#!/bin/sh\nexit 0\n')
        fake_curl.chmod(0o755)
        self.environment = {**os.environ, 'PATH': str(self.bin) + ':' + os.environ['PATH'],
                            'COMMAND_LOG': str(self.log)}
        self.make_archive()

    def make_archive(self, extra=None):
        files = {'deploy/compose.yaml': b'name: senpilot\n', 'deploy/Dockerfile': b'FROM scratch\n',
                 'pyproject.toml': b'', 'tests/test_stub.py': b''}
        if extra:
            files.update(extra)
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, content in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(content)
                archive.addfile(entry, io.BytesIO(content))

    def deploy(self, **environment):
        return subprocess.run(['bash', str(RELEASE_SCRIPT), REVISION, str(self.archive), str(self.base)],
                              env={**self.environment, **environment}, capture_output=True, text=True)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_success_builds_and_tests_before_stop_then_records_revision(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        build = next(i for i, c in enumerate(commands) if c['args'][0] == 'build')
        tests = next(i for i, c in enumerate(commands) if 'unittest' in c['args'])
        stop = next(i for i, c in enumerate(commands) if 'stop' in c['args'])
        self.assertLess(build, tests)
        self.assertLess(tests, stop)
        self.assertEqual((self.base / 'deployed-revision').read_text().strip(), REVISION)
        self.assertEqual((self.base / 'current').resolve(), self.base / 'releases' / REVISION)
        self.assertEqual(len(list((self.base / 'backups').glob('*.tar.gz'))), 1)
        self.assertTrue((self.base / 'current' / '.env').is_symlink())

    def test_failed_start_restarts_previous_image_without_publishing_revision(self):
        result = self.deploy(FAIL_START='1')
        self.assertNotEqual(result.returncode, 0)
        up = [c for c in self.commands() if 'up' in c['args']]
        self.assertEqual(up[-1]['image'], 'senpilot-agent:previous')
        self.assertFalse((self.base / 'deployed-revision').exists())
        self.assertFalse((self.base / 'current').exists())
        self.assertEqual(len(list((self.base / 'backups').glob('*.tar.gz'))), 1)

    def test_failed_build_keeps_running_application_untouched(self):
        result = self.deploy(FAIL_BUILD='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('stop' in c['args'] for c in self.commands()))

    def test_path_traversal_archive_is_rejected(self):
        self.make_archive({'../../escaped': b'unsafe'})
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsafe release archive member', result.stderr)
        self.assertFalse((self.root / 'escaped').exists())
        self.assertFalse(any(c['args'][0] == 'build' for c in self.commands()))
