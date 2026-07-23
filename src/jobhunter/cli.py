# SPDX-License-Identifier: Apache-2.0
"""CLI entry point for JobHunter.

Commands:
  jobhunter run [--profile PATH]   Run the full ingest→filter→score→digest pipeline.
  jobhunter dismiss <id> [<id>…]   Permanently hide these listings from future digests.
  jobhunter undismiss <id>         Restore a previously dismissed listing.
  jobhunter dismissed              List all currently dismissed listing ids.

See: specs/02-functional-spec.md §Stage 7 (dismissal workflow)
     specs/04-technical-plan.md §Tech stack (CLI)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .digest import render_markdown
from .pipeline import run as pipeline_run
from .profile import ProfileError, load_profile

_DEFAULT_PROFILE = Path("profile.yaml")


def _build_adapters() -> list:
    """Build the list of configured source adapters from env vars.

    Currently supports Adzuna if ADZUNA_APP_ID and ADZUNA_APP_KEY are set.
    Prints a warning to stderr for each unconfigured adapter and returns an
    empty list when no adapters can be initialised.
    """
    adapters = []

    app_id = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")
    if app_id and app_key:
        from jobhunter.adapters.adzuna import AdzunaAdapter

        adapters.append(AdzunaAdapter(app_id=app_id, app_key=app_key))
    else:
        print(
            "Warning: ADZUNA_APP_ID / ADZUNA_APP_KEY not set — Adzuna adapter skipped.",
            file=sys.stderr,
        )

    return adapters


def _cmd_run(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile)
    try:
        profile = load_profile(profile_path)
    except ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    adapters = _build_adapters()

    listings, unknown_flags, report = pipeline_run(profile, adapters)

    digest = render_markdown(listings, report, unknown_flags)
    print(digest)
    return 0


def _cmd_dismiss(args: argparse.Namespace) -> int:
    print("dismiss: not yet implemented", file=sys.stderr)
    return 1


def _cmd_undismiss(args: argparse.Namespace) -> int:
    print("undismiss: not yet implemented", file=sys.stderr)
    return 1


def _cmd_dismissed(args: argparse.Namespace) -> int:
    print("dismissed: not yet implemented", file=sys.stderr)
    return 1


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
    run_p.set_defaults(func=_cmd_run)

    dismiss_p = sub.add_parser("dismiss", help="Permanently hide listings from future digests.")
    dismiss_p.add_argument("ids", nargs="+", metavar="ID", help="Listing id(s) to dismiss.")
    dismiss_p.set_defaults(func=_cmd_dismiss)

    undismiss_p = sub.add_parser("undismiss", help="Restore a previously dismissed listing.")
    undismiss_p.add_argument("id", metavar="ID", help="Listing id to undismiss.")
    undismiss_p.set_defaults(func=_cmd_undismiss)

    dismissed_p = sub.add_parser("dismissed", help="List all currently dismissed listing ids.")
    dismissed_p.set_defaults(func=_cmd_dismissed)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
