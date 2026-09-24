"""Runs every offline check in one go (no TigerGraph, no LLM keys needed):
answer-file validation, the TigerGraph/MCP code path, the graph-algorithm invariance proof,
LLM failover, and the UI smoke test.   python -m tests.run_all"""
import os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS = [("answer files", [sys.executable, "scripts/validate_answers.py"]),
          ("TigerGraph/MCP code path", [sys.executable, "-m", "tests.test_tigergraph_path"]),
          ("graph algorithm is context only", [sys.executable, "-m", "tests.test_graph_algorithm"]),
          ("LLM failover", [sys.executable, "-m", "tests.test_llm_failover"]),
          ("UI smoke test", [sys.executable, "-m", "tests.test_ui_smoke"])]

if __name__ == "__main__":
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    failed = 0
    for name, cmd in CHECKS:
        t0 = time.time()
        r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
        ok = r.returncode == 0 and not any("FAIL" in l for l in lines[-3:])
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<34} {time.time() - t0:6.1f}s  {lines[-1] if lines else r.stderr.strip()[-200:]}")
    print("ALL PASS" if not failed else f"{failed} check(s) failed")
    sys.exit(1 if failed else 0)
