import sys
from pathlib import Path
import os

# Ensure src/ is in the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from slicing_dashboard.app import app

# Vercel requires the underlying Flask server to be exposed
server = app.server
app = server
