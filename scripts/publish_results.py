"""Publish append-only results on current remote HEAD; rebuild derived views.

Never rebase generated HTML/JSON. Never force push or overwrite a historical file.
The original runner checkout is retained for artifact upload on every failure.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PATHS = ['predictions', 'evaluations/v1', 'evaluations/v2', 'evaluations/v3', 'output/evaluations',
         'data/universes', 'data/benchmarks', 'data/fundamentals/v3', 'records']


def git(root, *args, check=True):
    return subprocess.run(['git', '-C', str(root), *args], check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def publish(root, branch, message):
    root = Path(root).resolve()
    git(root, 'check-ref-format', 'refs/heads/' + branch)
    # Capture only additions/changes produced in this checkout, never copy old
    # files over newer remote history. Missing files are not propagated as deletes.
    changed = git(root, 'diff', '--name-only', '-z', 'HEAD', '--', *PATHS).stdout
    added = git(root, 'ls-files', '--others', '--exclude-standard', '-z', '--', *PATHS).stdout
    names = {os.fsdecode(n) for n in (changed + added).split(b'\0') if n}
    with tempfile.TemporaryDirectory(prefix='forecast-publish-') as tmp:
        tmp = Path(tmp)
        snapshot = tmp / 'snapshot'
        for name in names:
            source = root / name
            if source.is_symlink():
                raise RuntimeError('Refusing symbolic link: ' + name)
            if source.is_file():
                dest = snapshot / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
        for attempt in range(1, 5):
            git(root, 'fetch', 'origin', 'refs/heads/' + branch)
            base = git(root, 'rev-parse', 'FETCH_HEAD').stdout.decode().strip()
            work = tmp / ('work-' + str(attempt))
            git(root, 'worktree', 'add', '--detach', str(work), base)
            try:
                if snapshot.exists():
                    for source in snapshot.rglob('*'):
                        if not source.is_file():
                            continue
                        name = source.relative_to(snapshot)
                        dest = work / name
                        if dest.is_symlink() or any(p.is_symlink() for p in dest.parents if p != work.parent):
                            raise RuntimeError('Refusing symbolic link: ' + str(name))
                        if dest.exists() and dest.read_bytes() != source.read_bytes():
                            raise RuntimeError('Historical file collision; retained both checkouts: ' + str(name))
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, dest)
                for module in ['dashboard.normalize', 'dashboard.build']:
                    subprocess.run([sys.executable, '-m', module], cwd=work, check=True)
                targets = [p for p in PATHS + ['docs/index.html', 'dashboard/data.json'] if (work / p).exists()]
                if targets:
                    git(work, 'add', '--', *targets)
                if git(work, 'diff', '--cached', '--quiet', check=False).returncode == 0:
                    print('Results already published.')
                    return
                git(work, '-c', 'user.name=github-actions[bot]', '-c',
                    'user.email=41898282+github-actions[bot]@users.noreply.github.com', 'commit', '-m', message)
                pushed = git(work, 'push', 'origin', 'HEAD:refs/heads/' + branch, check=False)
                if pushed.returncode == 0:
                    print('Results published on latest branch; dashboard rebuilt.')
                    return
                remote = git(root, 'ls-remote', '--heads', 'origin', 'refs/heads/' + branch).stdout.decode().split()
                if not remote or remote[0] == base:
                    raise RuntimeError('Push failed: ' + pushed.stderr.decode(errors='replace'))
                print('Remote advanced during publish; rebuilding on latest HEAD.', flush=True)
            finally:
                git(root, 'worktree', 'remove', '--force', str(work), check=False)
        raise RuntimeError('Remote changed repeatedly; results remain in original checkout/artifact.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--branch', required=True)
    parser.add_argument('--message', required=True)
    args = parser.parse_args()
    publish(Path.cwd(), args.branch, args.message)
