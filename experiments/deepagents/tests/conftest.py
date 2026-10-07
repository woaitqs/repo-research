import sys
from pathlib import Path

# Allow `pytest` from a fresh checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
