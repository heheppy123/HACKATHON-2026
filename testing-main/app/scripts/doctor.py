from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def hit(path: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=3) as resp:
            data = resp.read().decode("utf-8")
            return True, data
    except urllib.error.URLError as exc:
        return False, str(exc)


def main() -> None:
    print("FrostFlow Doctor")
    print("=" * 40)
    ok, out = hit("/health")
    if not ok:
        print("❌ Backend is not running at http://127.0.0.1:8000")
        print("Start it with: uvicorn app.main:app --reload")
        print(f"Details: {out}")
        return

    print("✅ Backend reachable")
    print("health:", out)

    for path in ["/risk-map?horizon_hours=0", "/maintenance-plan?horizon_hours=6", "/debug/status"]:
        ok, data = hit(path)
        if ok:
            parsed = json.loads(data)
            print(f"✅ {path} -> keys: {list(parsed.keys())}")
        else:
            print(f"❌ {path} -> {data}")

    print("\nOpen the live UI at: http://127.0.0.1:8000/web/")
    print("Do NOT open web/index.html directly with file://")


if __name__ == "__main__":
    main()
