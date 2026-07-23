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
import sys
from pathlib import Path

from .profile import ProfileError, load_profile

_DEFAULT_PROFILE = Path("profile.yaml")


def _cmd_run(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile)
    try:
        profile = load_profile(profile_path)
    except ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(
        f"Profile loaded: {len(profile['queries']['keywords'])} keyword(s), "
        f"{len(profile['queries']['locations'])} location(s)."
    )
    print("Pipeline not yet implemented — see IMPLEMENTATION_PLAN.md for next steps.")
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
