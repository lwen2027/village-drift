"""Run every test without pytest.

    python3 tests/run.py

pytest is not always installed, and running a test module directly only
*defines* its functions — it exits 0 in silence whether or not anything works.
That silence reads as success. This runner actually calls them.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def main() -> int:
    ok, fails = 0, []
    for name in sorted(f for f in os.listdir(HERE) if f.startswith("test_")):
        path = os.path.join(HERE, name)
        spec = importlib.util.spec_from_file_location(name[:-3], path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "setup_module"):
            mod.setup_module(mod)
        for fn in sorted(d for d in dir(mod) if d.startswith("test_")):
            try:
                getattr(mod, fn)()
                ok += 1
            except Exception:
                fails.append((name, fn, traceback.format_exc()))
        if hasattr(mod, "teardown_module"):
            mod.teardown_module(mod)

    for name, fn, tb in fails:
        print(f"\nFAIL {name}::{fn}\n{tb}")
    print(f"{ok} passed, {len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
