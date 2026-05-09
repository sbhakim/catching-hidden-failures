"""Pytest config — put the repo root on sys.path so ``import audit_layer``
works without installing the package."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
