"""Headless smoke test of the analyst console: every page must render without an exception
(overview, its ?f= filters, How it works, and all 20 case pages). No data or .env needed.
   python -m tests.test_ui_smoke"""
import sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from streamlit.testing.v1 import AppTest
pages = [("overview", {}), ("f=fraud", {"f": "fraud"}), ("f=uncertain", {"f": "uncertain"}), ("how", {"view": "how"})] + [(f"case {i:03d}", {"view": "case", "case": f"HHG-{i:03d}"}) for i in range(1, 21)]
bad = 0
for label, qp in pages:
    at = AppTest.from_file(os.path.join(ROOT, "ui", "app.py"), default_timeout=120)
    for k, v in qp.items(): at.query_params[k] = v
    at.run()
    errs = [e.value for e in at.exception]
    if errs: bad += 1
    print(f"{label:<10} {'EXCEPTION: ' + str(errs[0])[:300] if errs else 'ok'}  (markdown blocks: {len(at.markdown)})")
print("pages with exceptions:", bad)
print("RESULT:", "PASS" if bad == 0 else "FAIL")
sys.exit(1 if bad else 0)
