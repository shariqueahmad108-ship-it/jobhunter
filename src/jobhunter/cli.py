# SPDX-License-Identifier: Apache-2.0
"""CLI entry point for JobHunter.

Commands:
  jobhunter run [--profile PATH] [--state PATH]
                                   Run the full ingest→filter→score→digest pipeline.
  jobhunter dismiss <id> [<id>…]   Permanently hide listings from future digests.
  jobhunter undismiss <id>         Restore a previously dismissed listing.
  jobhunter dismissed              List all currently dismissed listing ids.

See: specs/02-functional-spec.md §Stage 7 (dismissal workflow)
     specs/04-technical-plan.md §Tech stack (CLI)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from .digest import render_csv_data, render_html, render_json_data, render_markdown
from .pipeline import run as pipeline_run
from .profile import ProfileError, effective_fx_rates, load_fx_rates, load_profile
from .state import (
    dismiss_ids,
    load_state,
    partition_results,
    save_state,
    undismiss_id,
    update_state,
)

_DEFAULT_PROFILE = Path("profile.yaml")
_DEFAULT_STATE = Path("state/state.yaml")


def _build_adapters(profile: dict) -> list:
    """Build the source adapters the PROFILE activates.

    Reads the unified ``sources:`` block when present; falls back to
    legacy ``queries.ats_watchlist`` (with a deprecation note) so old
    profiles keep working unchanged.

    sources.adzuna.enabled=false suppresses the Adzuna adapter even when
    env credentials are set.  Adapters for sources not yet implemented
    (feeds, remotive, remoteok, careerjet) emit a warning when enabled.
    """
    adapters = []
    sources = profile.get("sources") or {}

    # --- Adzuna -----------------------------------------------------------
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")
    adzuna_cfg = sources.get("adzuna")
    if adzuna_cfg is not None:
        if adzuna_cfg.get("enabled", True):
            if app_id and app_key:
                from jobhunter.adapters.adzuna import AdzunaAdapter

                adapters.append(AdzunaAdapter(app_id=app_id, app_key=app_key))
            else:
                print(
                    "Warning: sources.adzuna.enabled=true but "
                    "ADZUNA_APP_ID / ADZUNA_APP_KEY not set — Adzuna adapter skipped.",
                    file=sys.stderr,
                )
        # enabled=false: silently skip (user explicitly opted out)
    else:
        # Legacy behaviour: activate when env creds present
        if app_id and app_key:
            from jobhunter.adapters.adzuna import AdzunaAdapter

            adapters.append(AdzunaAdapter(app_id=app_id, app_key=app_key))
        else:
            print(
                "Warning: ADZUNA_APP_ID / ADZUNA_APP_KEY not set — Adzuna adapter skipped.",
                file=sys.stderr,
            )

    # --- ATS watchlist ----------------------------------------------------
    sources_watchlist = sources.get("ats_watchlist")
    queries_watchlist = profile.get("queries", {}).get("ats_watchlist") or []
    if sources_watchlist is not None:
        watchlist = sources_watchlist
    else:
        watchlist = queries_watchlist
        if queries_watchlist:
            print(
                "Deprecation: queries.ats_watchlist is deprecated; "
                "move entries to sources.ats_watchlist.",
                file=sys.stderr,
            )
    if watchlist:
        from jobhunter.adapters.ats import AtsAdapter

        adapters.append(AtsAdapter(watchlist))

    # --- Not-yet-implemented sources --------------------------------------
    feeds = sources.get("feeds") or []
    if feeds:
        print(
            "Warning: sources.feeds not yet implemented (rss-atom-adapter pending).",
            file=sys.stderr,
        )
    for src_name in ("remotive", "remoteok", "careerjet"):
        cfg = sources.get(src_name)
        if cfg and cfg.get("enabled"):
            print(
                f"Warning: sources.{src_name} not yet implemented "
                f"(remote-board-adapters / aggregator-adapter pending).",
                file=sys.stderr,
            )

    return adapters


def _profile_slug(profile_path: Path) -> str:
    """Multi-profile identity: '' for the default profile.yaml, else its stem.

    e.g. profile-ospo.yaml -> 'profile-ospo'. Used to keep each profile's
    seen-state and digest files independent without extra flags.
    """
    return "" if profile_path.stem == "profile" else profile_path.stem


def _cmd_run(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile)
    state_path = Path(args.state)
    slug_suffix = _profile_slug(profile_path)
    # A non-default profile gets its own state file automatically unless the
    # user explicitly chose one — two profiles must never share seen-state.
    if slug_suffix and str(state_path) == str(_DEFAULT_STATE):
        state_path = state_path.with_name(f"state-{slug_suffix}.yaml")

    try:
        profile = load_profile(profile_path)
    except ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    try:
        state = load_state(state_path)
    except ValueError as e:
        print(f"Error loading state: {e}", file=sys.stderr)
        return 1

    # FX rates are global (fx_rates.yaml at repo root); the profile may override
    # individual currencies. Injected here so filter/score read one merged table.
    profile["hard_requirements"]["fx_rates"] = effective_fx_rates(
        profile, load_fx_rates()
    )

    adapters = _build_adapters(profile)

    today = date.today().isoformat()
    dismissed = set(state.dismissed_ids)
    results, report = pipeline_run(profile, adapters, dismissed_ids=dismissed)

    output_cfg = profile.get("output", {})
    max_shown = int(output_cfg.get("max_shown", 25))
    show_prev = bool(output_cfg.get("show_previously_seen", True))
    fmt = str(output_cfg.get("format", "markdown"))  # markdown | html | both
    data_fmt = str(output_cfg.get("data_format", "json"))  # json | csv | both

    new_results, prev_results = partition_results(results, state)

    report.shown_new = len(new_results)
    report.shown_previous = len(prev_results)

    prev_for_render = prev_results if show_prev else None

    # Determine output directory: explicit --output-dir wins; default is a
    # peer directory named "digests" next to the state directory.
    if args.output_dir is not None:
        digest_dir = Path(args.output_dir)
    else:
        digest_dir = state_path.parent.with_name("digests")
    digest_dir.mkdir(parents=True, exist_ok=True)
    # Filename stem: date, plus the profile name for non-default profiles so
    # same-day runs of different profiles never overwrite each other.
    slug = f"{today}-{slug_suffix}" if slug_suffix else today

    # Write data file(s) in the configured format(s).
    if data_fmt in ("json", "both"):
        json_path = digest_dir / f"{slug}.json"
        json_data = render_json_data(new_results, prev_for_render)
        try:
            json_path.write_text(json_data, encoding="utf-8")
            print(f"Data file: {json_path}", file=sys.stderr)
        except OSError as e:
            print(f"Warning: could not write data file {json_path}: {e}", file=sys.stderr)

    if data_fmt in ("csv", "both"):
        csv_path = digest_dir / f"{slug}.csv"
        csv_data = render_csv_data(new_results, prev_for_render)
        try:
            csv_path.write_text(csv_data, encoding="utf-8")
            print(f"CSV data file: {csv_path}", file=sys.stderr)
        except OSError as e:
            print(f"Warning: could not write CSV file {csv_path}: {e}", file=sys.stderr)

    # Render and emit digest in the configured format(s).
    if fmt in ("markdown", "both"):
        md = render_markdown(
            new_results,
            report,
            previously_seen=prev_for_render,
            max_shown=max_shown,
            show_previously_seen=show_prev,
        )
        print(md)
        md_path = digest_dir / f"{slug}.md"
        try:
            md_path.write_text(md, encoding="utf-8")
        except OSError as e:
            print(f"Warning: could not write digest {md_path}: {e}", file=sys.stderr)

    if fmt in ("html", "both"):
        html_content = render_html(
            new_results,
            report,
            previously_seen=prev_for_render,
            max_shown=max_shown,
            show_previously_seen=show_prev,
        )
        html_path = digest_dir / f"{slug}.html"
        try:
            html_path.write_text(html_content, encoding="utf-8")
            print(f"HTML digest: {html_path}", file=sys.stderr)
        except OSError as e:
            print(f"Warning: could not write HTML digest {html_path}: {e}", file=sys.stderr)

    # Record what was actually RENDERED as seen (spec 02 §Stage 7, decided
    # 2026-07-23): overflow beyond max_shown is NOT recorded — those roles stay
    # eligible and resurface in the next run's shortlist.
    rendered_new = new_results[:max_shown]
    all_shown = rendered_new + (prev_results if show_prev else [])
    update_state(state, all_shown, today)
    try:
        save_state(state, state_path)
    except OSError as e:
        print(f"Warning: could not save state to {state_path}: {e}", file=sys.stderr)

    return 0


def _cmd_dismiss(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    try:
        state = load_state(state_path)
    except ValueError as e:
        print(f"Error loading state: {e}", file=sys.stderr)
        return 1

    state = dismiss_ids(state, list(args.ids))
    try:
        save_state(state, state_path)
    except OSError as e:
        print(f"Error saving state: {e}", file=sys.stderr)
        return 1

    for lid in args.ids:
        print(f"Dismissed: {lid}")
    return 0


def _cmd_undismiss(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    try:
        state = load_state(state_path)
    except ValueError as e:
        print(f"Error loading state: {e}", file=sys.stderr)
        return 1

    state = undismiss_id(state, args.id)
    try:
        save_state(state, state_path)
    except OSError as e:
        print(f"Error saving state: {e}", file=sys.stderr)
        return 1

    print(f"Undismissed: {args.id}")
    return 0


def _cmd_dismissed(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    try:
        state = load_state(state_path)
    except ValueError as e:
        print(f"Error loading state: {e}", file=sys.stderr)
        return 1

    if not state.dismissed_ids:
        print("No dismissed listings.")
    else:
        for lid in state.dismissed_ids:
            print(lid)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobhunter",
        description="Personal job-search pipeline — see specs/ for full documentation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run the ingest→filter→score→digest pipeline.")
    run_p.add_argument(
        "--profile",
        default=str(_DEFAULT_PROFILE),
        metavar="PATH",
        help=f"Path to profile.yaml (default: {_DEFAULT_PROFILE})",
    )
    run_p.add_argument(
        "--state",
        default=str(_DEFAULT_STATE),
        metavar="PATH",
        help=f"Path to run-state file (default: {_DEFAULT_STATE})",
    )
    run_p.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        dest="output_dir",
        help="Directory for digest and data-file output (default: digests/ peer to state dir)",
    )
    run_p.set_defaults(func=_cmd_run)

    dismiss_p = sub.add_parser("dismiss", help="Permanently hide listings from future digests.")
    dismiss_p.add_argument("ids", nargs="+", metavar="ID", help="Listing id(s) to dismiss.")
    dismiss_p.add_argument(
        "--state",
        default=str(_DEFAULT_STATE),
        metavar="PATH",
        help=f"Path to run-state file (default: {_DEFAULT_STATE})",
    )
    dismiss_p.set_defaults(func=_cmd_dismiss)

    undismiss_p = sub.add_parser("undismiss", help="Restore a previously dismissed listing.")
    undismiss_p.add_argument("id", metavar="ID", help="Listing id to undismiss.")
    undismiss_p.add_argument(
        "--state",
        default=str(_DEFAULT_STATE),
        metavar="PATH",
        help=f"Path to run-state file (default: {_DEFAULT_STATE})",
    )
    undismiss_p.set_defaults(func=_cmd_undismiss)

    dismissed_p = sub.add_parser("dismissed", help="List all currently dismissed listing ids.")
    dismissed_p.add_argument(
        "--state",
        default=str(_DEFAULT_STATE),
        metavar="PATH",
        help=f"Path to run-state file (default: {_DEFAULT_STATE})",
    )
    dismissed_p.set_defaults(func=_cmd_dismissed)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
