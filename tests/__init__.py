import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Unit tests never call a real engine CLI: the identity canary is exercised by
# tests/test_isolation.py with a fake runner.
os.environ.setdefault("AEO_CANARY", "skip")
