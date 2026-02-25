#!/usr/bin/env python3
"""
scripts/test_identity.py — standalone test for the identity resolution engine.

Runs a handful of known artists through identity_resolver.resolve_artist() against
the live Last.fm and iTunes APIs. Prints resolution results so you can visually
verify confidence scores and track overlap before enabling the feature in the CC pipeline.

Usage (from project root):
    python scripts/test_identity.py

Requirements:
  - LASTFM_API_KEY set in .env or environment
  - Internet access (calls Last.fm + iTunes APIs)
  - No app server needed

Add/remove artists in TEST_ARTISTS below.
Each entry: (last_fm_name, note)
  note = human-readable hint to help verify the result manually
"""
import sys
import os
import logging
from pathlib import Path

# Ensure project root is on the path so app.* imports work
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# Configure minimal logging so identity_resolver logs are visible
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s — %(message)s",
)
# Suppress chatty modules
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("requests").setLevel(logging.ERROR)


TEST_ARTISTS = [
    ("311",             "ska-punk band, 'Down', 'Amber', 'All Mixed Up'"),
    ("Ballyhoo!",       "reggae-rock, 'Buzzkill', 'Pineapple'"),
    ("Slightly Stoopid","reggae-rock, 'Closer to the Sun', 'Collie Man'"),
    ("The Dirty Heads", "reggae-rock, 'Lay Me Down', 'Dance All Night'"),
    ("Sublime",         "classic band, 'What I Got', 'Santeria', 'What I Got (Reprise)'"),
]


def _print_separator():
    print("-" * 70)


def _confidence_badge(confidence: int) -> str:
    if confidence >= 100:
        return "[✓✓✓ 100 — exact overlap 3+]"
    elif confidence >= 92:
        return "[✓✓  92  — 2 tracks overlap]"
    elif confidence >= 86:
        return "[✓   86  — 1 track overlap]"
    elif confidence >= 85:
        return "[✓   85+ — high]"
    else:
        return "[?   80  — name match only]"


def run_test(artist_name: str, note: str):
    from app import identity_resolver

    print(f"\nTesting: {artist_name}")
    print(f"  Hint : {note}")

    result = identity_resolver.resolve_artist(artist_name, force=True)

    conf = result["confidence"]
    level = result["confidence_level"]
    badge = _confidence_badge(conf)
    itunes_id = result.get("itunes_artist_id") or "(none)"
    itunes_name = result.get("itunes_artist_name") or ""
    reasons = ", ".join(result.get("reason_codes", []))

    print(f"  iTunes ID   : {itunes_id}")
    if itunes_name:
        print(f"  iTunes name : {itunes_name}")
    print(f"  Confidence  : {conf} {badge}")
    print(f"  Level       : {level}")
    print(f"  Reasons     : {reasons}")

    candidates = result.get("debug_candidates") or []
    if candidates:
        print("  Candidates  :")
        for c in candidates[:3]:
            overlap_str = f"  overlap={c['overlap']}" if c.get("overlap") is not None else ""
            print(f"    score={c['score']:>5}  id={c['id']}  name={c['name']}{overlap_str}")

    if level == "high":
        status = "PASS"
    elif level == "medium":
        status = "VERIFY — name match only, no track overlap (check iTunes ID manually)"
    else:
        status = "WARN  — low confidence, manual check needed"

    print(f"  Status      : {status}")


def main():
    print("=" * 70)
    print("Identity Resolver Test")
    print("Last.fm ↔ iTunes top-track overlap confidence scoring")
    print("=" * 70)

    # Import config to trigger load_dotenv and validate LASTFM_API_KEY
    from app import config
    if not config.LASTFM_API_KEY:
        print("\nERROR: LASTFM_API_KEY is not set — set it in .env or environment and retry.")
        sys.exit(1)

    print(f"Last.fm user : {config.LASTFM_USERNAME or '(any public artist endpoint — no user needed)'}")
    print(f"Artists      : {len(TEST_ARTISTS)}")

    for artist_name, note in TEST_ARTISTS:
        _print_separator()
        try:
            run_test(artist_name, note)
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(0)
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()

    _print_separator()
    print("\nDone. Review 'VERIFY' entries above and confirm iTunes IDs are correct.")
    print("All 'PASS' entries (confidence >= 85) will be used without re-checking during CC runs.")


if __name__ == "__main__":
    main()
