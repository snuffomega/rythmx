from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _bootstrap() -> None:
    from app.db import rythmx_store
    from app.plugins import load_plugins

    rythmx_store.init_db()
    slot_config = rythmx_store.get_all_plugin_slot_config()
    plugin_settings = rythmx_store.get_all_plugin_settings()
    load_plugins(slot_config=slot_config, plugin_settings=plugin_settings)


def _print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No rows")
        return
    print("status                 confidence  artist  album")
    print("-" * 90)
    for row in rows:
        status = str(row.get("match_status") or "unresolved")
        confidence = float(row.get("match_confidence") or 0.0)
        artist = str(row.get("artist_name") or "")
        album = str(row.get("album_name") or "")
        print(f"{status:<22} {confidence:>8.2f}  {artist}  {album}")


def _cmd_match_probe(args: argparse.Namespace) -> int:
    _bootstrap()
    from app.services import fetch_pipeline

    report = fetch_pipeline.probe_fetch_match(
        build_id=args.build_id,
        run_id=args.run_id,
        limit=args.limit,
    )

    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=False))
        return 0

    counts = report.get("counts") or {}
    print(
        "Fetch match probe "
        f"(provider={report.get('provider')} total={report.get('total')} "
        f"confident={counts.get('confident', 0)} "
        f"ambiguous={counts.get('ambiguous', 0)} "
        f"unresolved={counts.get('unresolved', 0)} "
        f"search_inconsistent={counts.get('search_inconsistent', 0)})"
    )
    _print_table(list(report.get("items") or []))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch tooling")
    sub_fetch = parser.add_subparsers(dest="group", required=True)

    match_parser = sub_fetch.add_parser("match", help="Fetch match tools")
    match_sub = match_parser.add_subparsers(dest="match_cmd", required=True)

    probe = match_sub.add_parser("probe", help="Dry-run fetch match proof")
    target = probe.add_mutually_exclusive_group(required=True)
    target.add_argument("--build-id", help="Forge build id to probe")
    target.add_argument("--run-id", help="Fetch run id to probe")
    probe.add_argument("--limit", type=int, default=200, help="Max items to probe")
    probe.add_argument("--json", dest="as_json", action="store_true", help="Output JSON")
    probe.set_defaults(func=_cmd_match_probe)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if not callable(func):
        parser.print_help()
        return 2
    return int(func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

