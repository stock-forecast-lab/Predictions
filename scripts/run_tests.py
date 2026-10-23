"""Run legacy and packaged engines in separate Python interpreters."""
from pathlib import Path
import subprocess,sys
root=Path(__file__).resolve().parents[1]
commands=[['-m','pytest','-q','tests','--ignore=tests/v2','--ignore=tests/v3'],
          ['-m','pytest','-q','tests/v2','-c','tests/v2/pytest.ini'],
          ['-m','pytest','-q','tests/v3','-c','tests/v3/pytest.ini']]
for command in commands:
    result=subprocess.run([sys.executable,*command],cwd=root)
    if result.returncode:raise SystemExit(result.returncode)
