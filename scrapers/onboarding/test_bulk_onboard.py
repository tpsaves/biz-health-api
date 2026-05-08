"""
Bulk onboarding smoke test: onboards 5 DFW restaurants from restaurants.csv,
runs all scrapers + scoring for each, and prints a per-restaurant summary.

Run inside the scrapers container:
    docker-compose exec scrapers python onboarding/test_bulk_onboard.py
"""
import logging
import sys
from pathlib import Path

# When run as `python onboarding/test_bulk_onboard.py`, Python sets sys.path[0]
# to /app/onboarding (the script's directory). The scraper packages (signals/,
# scoring/) live under /app, so we insert /app explicitly.
#
# In C# this isn't necessary because MSBuild copies all referenced assemblies
# into the output directory at build time. Python resolves imports at runtime
# from sys.path, so the caller controls the search path — equivalent to
# manually setting PYTHONPATH before running a .NET app that references a
# sibling project via a raw DLL path rather than a project reference.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.WARNING,  # suppress INFO noise from scrapers during test
    format="%(asctime)s %(levelname)s - %(message)s",
)

from onboarding.bulk_onboard import onboard_from_csv  # noqa: E402 — after sys.path fix

CSV_PATH = Path(__file__).parent / "restaurants.csv"

print("=" * 70)
print("Bulk Onboarding Test — 5 DFW Restaurants")
print("=" * 70)
print()

results = onboard_from_csv(str(CSV_PATH))

all_ok = True

for r in results:
    name = r["name"]
    rid  = r.get("restaurant_id", "—")
    print(f"  Restaurant : {name}")
    print(f"  ID         : {rid}")

    for step in r["steps"]:
        icon = "✓" if step["status"] == "OK" else "✗"
        print(
            f"    {icon} {step['step']:<22} {step['status']:<8}"
            f" {step['elapsed']:5.1f}s  {step['detail']}"
        )
        if step["status"] != "OK":
            all_ok = False

    if r.get("error"):
        print(f"    ✗ ERROR: {r['error']}")
        all_ok = False

    print()

print("=" * 70)
if all_ok:
    print("All restaurants onboarded successfully.")
else:
    print("One or more restaurants had failures — see details above.")
print("=" * 70)
