"""Compatibility entrypoint for the current local UI checks."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).with_name("check_market_ui.py")),run_name="__main__")
