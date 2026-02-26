import sys
from pathlib import Path

# Allow tests to import "backend.app" without installing the package.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
