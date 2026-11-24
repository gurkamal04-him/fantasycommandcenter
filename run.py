import os
import threading
from waitress import serve

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app import app, warm_rankings, _scheduler_loop

# Start background scheduler daemon thread
threading.Thread(target=_scheduler_loop, daemon=True).start()

# Warm up rankings for the default season
threading.Thread(target=warm_rankings, args=("season", "2026-27"), daemon=True).start()

# Fewer threads than a modern PC, to save memory on the laptop
serve(app, host="127.0.0.1", port=5000, threads=8)
