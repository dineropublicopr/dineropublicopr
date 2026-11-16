"""Pone pipeline/ en sys.path para que las pruebas importen los modulos igual que run.py."""

import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))
