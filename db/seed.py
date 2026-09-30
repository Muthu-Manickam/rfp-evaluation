import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rfp_evaluation import store

reset = "--reset" in sys.argv
store.setup(reset_criteria=reset)
print(f"Database: {store.db_path()}")
for c in store.get_criteria():
    print(f"  {c['criterion_id']}. {c['name']:<24} {c['weight']:g}%  max {c['max_score']:g}")
