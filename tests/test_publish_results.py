"""Publish from stale checkouts using a real local bare Git remote."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
spec = importlib.util.spec_from_file_location('publisher', Path(__file__).resolve().parents[1] / 'scripts/publish_results.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()

class PublishTests(unittest.TestCase):
    def fixture(self, base):
        remote, original, other = [base / n for n in ['remote.git', 'original', 'other']]
        git(base, 'init', '--bare', str(remote))
        git(base, 'clone', str(remote), str(original))
        git(original, 'config', 'user.name', 'Test')
        git(original, 'config', 'user.email', 'test@example.invalid')
        git(original, 'checkout', '-b', 'integration/test')
        (original / 'dashboard').mkdir()
        (original / 'dashboard/__init__.py').write_text('')
        (original / 'dashboard/normalize.py').write_text('')
        (original / 'dashboard/build.py').write_text("from pathlib import Path\np=Path('docs/index.html');p.parent.mkdir(exist_ok=True);p.write_text('|'.join(sorted(x.name for x in Path('predictions').glob('*.csv'))))\n")
        (original / 'predictions').mkdir()
        (original / 'predictions/old.csv').write_text('old')
        git(original, 'add', '.')
        git(original, 'commit', '-m', 'base')
        git(original, 'push', '-u', 'origin', 'integration/test')
        git(base, 'clone', '--branch', 'integration/test', str(remote), str(other))
        git(other, 'config', 'user.name', 'Test')
        git(other, 'config', 'user.email', 'test@example.invalid')
        return original, other

    def test_stale_checkout_rebuilds_with_both_results(self):
        with tempfile.TemporaryDirectory() as t:
            original, other = self.fixture(Path(t))
            (original / 'predictions/local.csv').write_text('local')
            (original / 'docs').mkdir()
            (original / 'docs/index.html').write_text('old local view')
            (other / 'predictions/remote.csv').write_text('remote')
            (other / 'docs').mkdir()
            (other / 'docs/index.html').write_text('old remote view')
            git(other, 'add', '.')
            git(other, 'commit', '-m', 'concurrent change')
            git(other, 'push')
            module.publish(original, 'integration/test', 'results')
            git(other, 'pull', '--ff-only')
            self.assertEqual((other / 'docs/index.html').read_text(), 'local.csv|old.csv|remote.csv')
            self.assertEqual((other / 'predictions/remote.csv').read_text(), 'remote')
            self.assertEqual((original / 'predictions/local.csv').read_text(), 'local')

    def test_collision_never_overwrites_history(self):
        with tempfile.TemporaryDirectory() as t:
            original, other = self.fixture(Path(t))
            (original / 'predictions/same.csv').write_text('local')
            (other / 'predictions/same.csv').write_text('remote')
            git(other, 'add', '.')
            git(other, 'commit', '-m', 'remote result')
            git(other, 'push')
            with self.assertRaisesRegex(RuntimeError, 'Historical file collision'):
                module.publish(original, 'integration/test', 'results')
            self.assertEqual((other / 'predictions/same.csv').read_text(), 'remote')
            self.assertEqual((original / 'predictions/same.csv').read_text(), 'local')

    def test_retry_when_remote_advances_at_push(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as t:
            original, other = self.fixture(Path(t))
            (original / 'predictions/local.csv').write_text('local')
            original_git = module.git
            raced = []
            def concurrent(root, *args, **kwargs):
                if args and args[0] == 'push' and not raced:
                    raced.append(True)
                    (other / 'predictions/race.csv').write_text('remote during push')
                    git(other, 'add', '.')
                    git(other, 'commit', '-m', 'racing publisher')
                    git(other, 'push')
                return original_git(root, *args, **kwargs)
            with patch.object(module, 'git', side_effect=concurrent):
                module.publish(original, 'integration/test', 'results')
            git(other, 'pull', '--ff-only')
            self.assertEqual((other / 'docs/index.html').read_text(), 'local.csv|old.csv|race.csv')
            self.assertTrue((other / 'predictions/race.csv').exists())

if __name__ == '__main__':
    unittest.main()
