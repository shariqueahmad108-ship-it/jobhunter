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
import logging
import os
import sys
from datetime import date
from pathlib import Path

from . import __version__, doctor
from .digest import render_csv_data, render_html, render_json_data, render_markdown
from .ingest import SourceAdapter
from .model import SourceStat
from .pipeline import run as pipeline_run
from .pipeline import run_with_snapshot as pipeline_run_with_snapshot
from .probe import format_probe_result, probe_check, probe_single
from .profile import (
    ProfileError,
    effective_fx_rates,
    fx_rates_age_days,
    load_fx_rates,
    load_profile,
)
from .source_stats import StatsRun, append_run, format_json, format_table, load_stats, save_stats
from .state import (
    IdError,
    dismiss_ids,
    known_ids,
    load_state,
    partition_results,
    resolve_listing_id,
    save_state,
    undismiss_id,
    update_state,
)

_DEFAULT_PROFILE = Path("profile.yaml")
_DEFAULT_STATE = Path("state/state.yaml")

logger = logging.getLogger(__name__)


def _configure_logging(verbose: bool) -> None:
    """Point the CLI's logger at stderr: INFO with -v, WARNING (today's
    default) otherwise.

    Called from _cmd_run rather than main() — main() is a two-line
    parse-and-dispatch shim, and the test suite calls args.func(args)
    directly without going through it, so configuring there would leave
    every test running with no handler attached at all.

    Re-configures (clearing any handler this logger already has) rather
    than checking-then-adding, so repeated calls in the same process —
    every test in this suite, for instance — don't stack up duplicate
    handlers and print each line twice.
    """
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO if verbose else logging.WARNING)
    logger.propagate = False


def _plural(n: int) -> str:
    return "" if n == 1 else "s"


def _log_source_progress(source_stats: list[SourceStat]) -> None:
    """One INFO line per source explaining what it did this run — the four
    cases from the issue (ok, failed, filtered out, and the zero-fetched
    case) are otherwise indistinguishable from the digest header's
    aggregate counts alone. Only visible with -v; see _configure_logging.
    """
    for stat in source_stats:
        requests = f"{stat.requests} request{_plural(stat.requests)}"
        if stat.failed:
            logger.info("%s: 0 fetched — %s", stat.name, stat.error)
        elif stat.fetched and not stat.passed_filter:
            logger.info(
                "%s: %d fetched (%s) — all filtered out", stat.name, stat.fetched, requests
            )
        else:
            logger.info("%s: %d fetched (%s)", stat.name, stat.fetched, requests)


def _active_source_names(profile: dict) -> set[str]:
    """Return source names that are active in this profile (without instantiating adapters)."""
    names: set[str] = set()
    sources = profile.get("sources") or {}

    adzuna_cfg = sources.get("adzuna")
    if adzuna_cfg is None or adzuna_cfg.get("enabled", True):
        names.add("adzuna")

    watchlist = (
        sources.get("ats_watchlist") or profile.get("queries", {}).get("ats_watchlist") or []
    )
    if watchlist:
        names.add("ats")

    if sources.get("feeds"):
        names.add("rss")

    if (sources.get("remotive") or {}).get("enabled"):
        names.add("remotive")

    if (sources.get("remoteok") or {}).get("enabled"):
        names.add("remoteok")

    if (sources.get("jooble") or {}).get("enabled"):
        names.add("jooble")

    return names


def _stats_path(state_path: Path, slug_suffix: str) -> Path:
    """Return the source stats file path for the given state path and profile slug."""
    filename = "source_stats.json" if not slug_suffix else f"source_stats-{slug_suffix}.json"
    return state_path.parent / filename


def _build_adapters(profile: dict) -> list[SourceAdapter]:
    """Build the source adapters the PROFILE activates.

    ALL activation lives in the unified ``sources:`` block (spec 03):
      sources.adzuna         {enabled, country}     creds from env
      sources.ats_watchlist  [{ats, slug, name}]    greenhouse|lever|ashby|workday
      sources.feeds          [{name, url}]          RSS/Atom, no creds
      sources.remotive       {enabled, categories}
      sources.remoteok       {enabled}

    Legacy fallback: ``queries.ats_watchlist`` still activates the ATS adapter
    when ``sources.ats_watchlist`` is absent (deprecated — move it to sources:).
    A source that is enabled but missing its credential warns and is skipped;
    a source absent or enabled=false is never constructed.
    """
    adapters: list[SourceAdapter] = []
    sources = profile.get("sources") or {}

    # --- Adzuna -----------------------------------------------------------
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")
    adzuna_cfg = sources.get("adzuna")
    adzuna_enabled = adzuna_cfg.get("enabled", True) if adzuna_cfg is not None else True
    if adzuna_cfg is not None and not adzuna_enabled:
        pass  # explicitly opted out — silent
    elif app_id and app_key:
        from jobhunter.adapters.adzuna import AdzunaAdapter

        adapters.append(AdzunaAdapter(app_id=app_id, app_key=app_key))
    else:
        print(
            "Warning: ADZUNA_APP_ID / ADZUNA_APP_KEY not set — Adzuna adapter skipped.",
            file=sys.stderr,
        )

    # --- ATS company watchlist (greenhouse/lever/ashby/workday) -----------
    watchlist = sources.get("ats_watchlist")
    if watchlist is None:
        watchlist = profile.get("queries", {}).get("ats_watchlist") or []
        if watchlist:
            print(
                "Note: queries.ats_watchlist is deprecated — move it to sources.ats_watchlist.",
                file=sys.stderr,
            )
    if watchlist:
        from jobhunter.adapters.ats import AtsAdapter

        adapters.append(AtsAdapter(watchlist))

    # --- RSS/Atom feeds ---------------------------------------------------
    feeds = sources.get("feeds") or []
    if feeds:
        from jobhunter.adapters.rss import FeedAdapter

        adapters.append(FeedAdapter(feeds))

    # --- Remotive ---------------------------------------------------------
    remotive_cfg = sources.get("remotive") or {}
    if remotive_cfg.get("enabled"):
        from jobhunter.adapters.remotive import RemotiveAdapter

        adapters.append(RemotiveAdapter(categories=remotive_cfg.get("categories") or []))

    # --- RemoteOK ---------------------------------------------------------
    if (sources.get("remoteok") or {}).get("enabled"):
        from jobhunter.adapters.remoteok import RemoteOKAdapter

        adapters.append(RemoteOKAdapter())

    # --- Jooble -----------------------------------------------------------
    if (sources.get("jooble") or {}).get("enabled"):
        jooble_key = os.environ.get("JOOBLE_API_KEY", "")
        if jooble_key:
            from jobhunter.adapters.jooble import JoobleAdapter

            adapters.append(JoobleAdapter(api_key=jooble_key))
        else:
            print(
                "Warning: sources.jooble enabled but JOOBLE_API_KEY "
                "not set — Jooble adapter skipped.",
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
    _configure_logging(args.verbose)
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
    _fx_path = "fx_rates.yaml"
    profile["hard_requirements"]["fx_rates"] = effective_fx_rates(profile, load_fx_rates(_fx_path))
    _fx_age = fx_rates_age_days(_fx_path)
    _FX_STALE_DAYS = 90

    adapters = _build_adapters(profile)

    today = date.today().isoformat()
    dismissed = set(state.dismissed_ids)
    keep_raw = bool(profile.get("output", {}).get("keep_raw", True))
    if keep_raw:
        results, report, pre_filter = pipeline_run_with_snapshot(
            profile, adapters, dismissed_ids=dismissed
        )
    else:
        results, report = pipeline_run(profile, adapters, dismissed_ids=dismissed)
        pre_filter = None

    _log_source_progress(report.source_stats)

    if _fx_age is not None and _fx_age >= _FX_STALE_DAYS:
        print(
            f"Warning: fx_rates.yaml is {_fx_age} days old — consider refreshing exchange rates.",
            file=sys.stderr,
        )
        report.fx_rates_stale_days = _fx_age

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

    # Write pre-filter snapshot when keep_raw is enabled (default true).
    if keep_raw and pre_filter is not None:
        from .snapshot import write_snapshot

        snap_path = digest_dir / f"{slug}.raw.json"
        try:
            write_snapshot(snap_path, report.run_at, profile, pre_filter)
            print(f"Snapshot: {snap_path}", file=sys.stderr)
        except OSError as e:
            print(f"Warning: could not write snapshot {snap_path}: {e}", file=sys.stderr)

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

    # Finalize shown counts and persist source stats.
    shown_by_source: dict[str, int] = {}
    for r in all_shown:
        for src in r.listing.sources:
            shown_by_source[src.name] = shown_by_source.get(src.name, 0) + 1
    for stat in report.source_stats:
        stat.shown = shown_by_source.get(stat.name, 0)

    sp = _stats_path(state_path, slug_suffix)
    try:
        history = load_stats(sp)
        history = append_run(history, StatsRun(run_at=report.run_at, sources=report.source_stats))
        save_stats(history, sp)
    except Exception as e:
        print(f"Warning: could not save source stats to {sp}: {e}", file=sys.stderr)

    return 0


def _cmd_dismiss(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    try:
        state = load_state(state_path)
    except ValueError as e:
        print(f"Error loading state: {e}", file=sys.stderr)
        return 1

    # The digest only ever shows an 8-char prefix, so that is what a user
    # copies. Resolve every id BEFORE writing anything: a batch either applies
    # whole or not at all, and an id that matches nothing is an error rather
    # than a silently-stored string that never drops a listing.
    candidates = known_ids(state)
    resolved: list[str] = []
    for raw in args.ids:
        try:
            resolved.append(resolve_listing_id(raw, candidates))
        except IdError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Ids come from a digest — run `jobhunter run` first, "
                "or paste the full id from the .json digest.",
                file=sys.stderr,
            )
            return 1

    state = dismiss_ids(state, resolved)
    try:
        save_state(state, state_path)
    except OSError as e:
        print(f"Error saving state: {e}", file=sys.stderr)
        return 1

    for raw, lid in zip(args.ids, resolved):
        suffix = "" if raw.strip().lower() == lid else f" (matched {raw})"
        print(f"Dismissed: {lid}{suffix}")
    return 0


def _cmd_undismiss(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    try:
        state = load_state(state_path)
    except ValueError as e:
        print(f"Error loading state: {e}", file=sys.stderr)
        return 1

    # Resolve against the dismissed set only: undismissing something that was
    # never dismissed is a typo, not a no-op to shrug at.
    try:
        lid = resolve_listing_id(args.id, state.dismissed_ids)
    except IdError as e:
        print(f"Error: {e}", file=sys.stderr)
        print("Run `jobhunter dismissed` to see what is currently dismissed.", file=sys.stderr)
        return 1
    if lid not in state.dismissed_ids:
        print(f"Error: id {args.id!r} is not currently dismissed", file=sys.stderr)
        return 1

    state = undismiss_id(state, lid)
    try:
        save_state(state, state_path)
    except OSError as e:
        print(f"Error saving state: {e}", file=sys.stderr)
        return 1

    print(f"Undismissed: {lid}")
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


def _cmd_sources(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile)
    state_path = Path(args.state)
    slug_suffix = _profile_slug(profile_path)
    if slug_suffix and str(state_path) == str(_DEFAULT_STATE):
        state_path = state_path.with_name(f"state-{slug_suffix}.yaml")

    sp = _stats_path(state_path, slug_suffix)
    try:
        history = load_stats(sp)
    except ValueError as e:
        print(f"Error loading source stats: {e}", file=sys.stderr)
        return 1

    last_n: int | None = getattr(args, "last", None)

    active: set[str] | None = None
    try:
        profile = load_profile(profile_path)
        active = _active_source_names(profile)
    except Exception:
        pass  # profile absent or invalid — omit inactive marking

    if args.json:
        print(format_json(history, last_n=last_n))
    else:
        print(format_table(history, last_n=last_n, active_sources=active))
    return 0


def _print_diff(
    current: list,
    other: list,
) -> None:
    """Print a textual diff between two replay shortlists."""
    current_map = {r.listing.id: r for r in current}
    other_map = {r.listing.id: r for r in other}

    entered = [r for lid, r in current_map.items() if lid not in other_map]
    left = [r for lid, r in other_map.items() if lid not in current_map]
    moved = []
    for lid, c in current_map.items():
        if lid in other_map:
            o = other_map[lid]
            if abs(c.score - o.score) > 0.01 or c.rank != o.rank:
                moved.append((c, o))

    print("\n=== Diff (current vs other) ===")
    print(f"Entered (now shown, wasn't): {len(entered)}")
    for r in entered[:20]:
        print(f"  #{r.rank}  {r.listing.title} @ {r.listing.company}  (score: {r.score:.0f})")
    print(f"Left (was shown, not now): {len(left)}")
    for r in left[:20]:
        print(f"  #{r.rank}  {r.listing.title} @ {r.listing.company}  (score: {r.score:.0f})")
    print(f"Moved (rank/score changed): {len(moved)}")
    for c, o in moved[:20]:
        print(
            f"  {c.listing.title} @ {c.listing.company}"
            f"  (was #{o.rank}/{o.score:.0f}, now #{c.rank}/{c.score:.0f})"
        )


def _replay_shortlist(
    snap: dict,
    profile: dict,
    dismissed: set,
) -> tuple:
    """Re-run Stages 4-7 on a snapshot's listing set. No network access."""
    from datetime import date as _date

    from .filter import run as filter_run
    from .model import RunReport
    from .rank import run as rank_run
    from .score import run as score_run

    run_date = _date.fromisoformat(snap["run_at"][:10])
    listings = snap["listings"]

    filter_result = filter_run(listings, profile, dismissed_ids=dismissed, today=run_date)
    tally = filter_result.tally
    scored = score_run(
        filter_result.passed,
        profile,
        unknown_flags=filter_result.unknown_flags,
        today=run_date,
    )
    shortlist, below_threshold = rank_run(scored, profile)

    weights_cfg: dict = profile.get("weights", {})
    active_weights = {k: float(v) for k, v in weights_cfg.items() if float(v) > 0}

    sources_used = sorted({s.name for listing in listings for s in listing.sources})

    report = RunReport(
        run_at=snap["run_at"],
        sources_used=sources_used,
        requests_made=0,
        ingested_count=len(listings),
        after_dedupe=len(listings),
        dropped_by_location=tally.by_location,
        dropped_by_seniority=tally.by_seniority,
        dropped_by_salary=tally.by_salary,
        dropped_by_employment=tally.by_employment,
        dropped_by_keyword=tally.by_keyword,
        dropped_by_required=tally.by_required,
        dropped_by_age=tally.by_age,
        dropped_dismissed=tally.dismissed,
        below_threshold=below_threshold,
        shown_new=len(shortlist),
        active_weights=active_weights,
    )
    return shortlist, report


def _cmd_replay(args: argparse.Namespace) -> int:
    from .snapshot import apply_set_overrides, load_snapshot

    try:
        snap = load_snapshot(Path(args.run_file))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Determine profile: explicit --profile wins, else use the stored snapshot.
    if args.profile:
        try:
            profile = load_profile(Path(args.profile))
        except ProfileError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    else:
        profile = snap["profile_snapshot"]

    # Apply --set overrides
    if args.set:
        profile = apply_set_overrides(profile, args.set)

    # Load state for dismissed_ids — replay is strictly read-only.
    state_path = Path(args.state)
    try:
        state = load_state(state_path)
    except ValueError as e:
        print(f"Error loading state: {e}", file=sys.stderr)
        return 1
    dismissed = set(state.dismissed_ids)

    shortlist, report = _replay_shortlist(snap, profile, dismissed)

    # --diff: compare against another snapshot replayed with the same settings.
    if args.diff:
        try:
            other_snap = load_snapshot(Path(args.diff))
        except ValueError as e:
            print(f"Error loading diff file: {e}", file=sys.stderr)
            return 1

        if args.profile:
            other_profile = load_profile(Path(args.profile))
        else:
            other_profile = other_snap["profile_snapshot"]
        if args.set:
            other_profile = apply_set_overrides(other_profile, args.set)

        other_shortlist, _ = _replay_shortlist(other_snap, other_profile, dismissed)
        _print_diff(shortlist, other_shortlist)

    # Render the replay digest (no new/prev split — all results shown together).
    output_cfg = profile.get("output", {})
    max_shown = int(output_cfg.get("max_shown", 25))

    md = render_markdown(
        shortlist,
        report,
        previously_seen=None,
        max_shown=max_shown,
        show_previously_seen=False,
    )

    if args.out:
        try:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(md, encoding="utf-8")
            print(f"Replay digest: {args.out}", file=sys.stderr)
        except OSError as e:
            print(f"Warning: could not write {args.out}: {e}", file=sys.stderr)
    else:
        print(md)

    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    if args.check and args.target:
        print("Error: --check cannot be combined with a target URL/slug.", file=sys.stderr)
        return 1
    if not args.check and not args.target:
        print("Error: provide a target URL/slug or use --check.", file=sys.stderr)
        return 1

    if args.check:
        profile_path = Path(args.profile)
        try:
            profile = load_profile(profile_path)
        except ProfileError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        sources = profile.get("sources") or {}
        watchlist = sources.get("ats_watchlist") or []
        if not watchlist:
            watchlist = profile.get("queries", {}).get("ats_watchlist") or []

        if not watchlist:
            print("No ats_watchlist entries found in profile.")
            return 0

        statuses = probe_check(watchlist)
        has_dead = False
        for status in statuses:
            entry = status.entry
            name = entry.get("name") or (entry.get("slug") or "").replace("-", " ").title()
            ats_type = entry.get("ats", "")
            slug = entry.get("slug", "")
            if status.result:
                print(format_probe_result(status.result, name=name))
            else:
                print(f"dead: {ats_type}/{slug} ({name}) — not confirmed")
                has_dead = True
        return 1 if has_dead else 0

    # Single-target mode
    results = probe_single(args.target, ats_hint=args.ats)
    if not results:
        print(f"not confirmed: {args.target}")
        return 0
    for result in results:
        print(format_probe_result(result))
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Answer 'is anything broken' about the config and every enabled source.

    Deliberately does one live query per source: that is the only way to tell a
    dead board from a quiet one, and the reason this exists rather than being
    folded into `run`. See specs/05-operator-tooling.md §5.4.
    """
    profile_path = Path(args.profile)
    state_path = Path(args.state)
    slug_suffix = _profile_slug(profile_path)
    if slug_suffix and str(state_path) == str(_DEFAULT_STATE):
        state_path = state_path.with_name(f"state-{slug_suffix}.yaml")

    checks: list[doctor.Check] = []
    profile_check, profile = doctor.check_profile(profile_path, load_profile)
    checks.append(profile_check)

    if profile is None:
        # Nothing downstream is meaningful without a valid profile.
        print(doctor.format_json(checks) if args.json else doctor.format_table(checks))
        return doctor.exit_code(checks)

    _fx_path = "fx_rates.yaml"
    checks.append(doctor.check_fx_rates(_fx_path, fx_rates_age_days(_fx_path)))
    checks.append(doctor.check_state(state_path, load_state))
    checks.extend(doctor.check_credentials(profile))

    if not args.offline:
        checks.extend(doctor.check_sources(_build_adapters(profile), profile))
        sources = profile.get("sources") or {}
        watchlist = sources.get("ats_watchlist") or profile.get("queries", {}).get(
            "ats_watchlist"
        ) or []
        checks.extend(doctor.check_boards(watchlist, probe_check))

    print(doctor.format_json(checks) if args.json else doctor.format_table(checks))
    return doctor.exit_code(checks)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobhunter",
        description="Personal job-search pipeline — see specs/ for full documentation.",
    )
    # Bug reports ask for a version; the tool has to be able to answer.
    parser.add_argument(
        "--version",
        action="version",
        version=f"jobhunter {__version__}",
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
    run_p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Explain what each source did this run (fetched/failed/filtered) on stderr",
    )
    run_p.set_defaults(func=_cmd_run)

    dismiss_p = sub.add_parser("dismiss", help="Permanently hide listings from future digests.")
    dismiss_p.add_argument(
        "ids",
        nargs="+",
        metavar="ID",
        help="Listing id(s) to dismiss — the short id shown in the digest is enough.",
    )
    dismiss_p.add_argument(
        "--state",
        default=str(_DEFAULT_STATE),
        metavar="PATH",
        help=f"Path to run-state file (default: {_DEFAULT_STATE})",
    )
    dismiss_p.set_defaults(func=_cmd_dismiss)

    undismiss_p = sub.add_parser("undismiss", help="Restore a previously dismissed listing.")
    undismiss_p.add_argument(
        "id",
        metavar="ID",
        help="Listing id to undismiss (short id from `jobhunter dismissed` is enough).",
    )
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

    sources_p = sub.add_parser("sources", help="Show per-source contribution stats across runs.")
    sources_p.add_argument(
        "--profile",
        default=str(_DEFAULT_PROFILE),
        metavar="PATH",
        help=f"Path to profile.yaml (default: {_DEFAULT_PROFILE})",
    )
    sources_p.add_argument(
        "--state",
        default=str(_DEFAULT_STATE),
        metavar="PATH",
        help=f"Path to run-state file (default: {_DEFAULT_STATE})",
    )
    sources_p.add_argument(
        "--last",
        type=int,
        default=None,
        metavar="N",
        help="Limit to the most recent N runs.",
    )
    sources_p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a table.",
    )
    sources_p.set_defaults(func=_cmd_sources)
    replay_p = sub.add_parser(
        "replay",
        help="Re-run Stages 4-7 offline from a saved snapshot (no network access).",
    )
    replay_p.add_argument(
        "run_file",
        metavar="RUN-FILE",
        help="Path to a .raw.json snapshot written by a previous run.",
    )
    replay_p.add_argument(
        "--profile",
        default=None,
        metavar="PATH",
        help="Alternate profile to use instead of the snapshot's stored profile.",
    )
    replay_p.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        default=None,
        help="Override a profile setting (dot-path, e.g. output.display_threshold=65). "
        "May be repeated.",
    )
    replay_p.add_argument(
        "--diff",
        default=None,
        metavar="RUN-FILE",
        help="Compare against another snapshot file and report entered/left/moved listings.",
    )
    replay_p.add_argument(
        "--out",
        default=None,
        metavar="FILE",
        help="Write the replay digest to FILE instead of stdout.",
    )
    replay_p.add_argument(
        "--state",
        default=str(_DEFAULT_STATE),
        metavar="PATH",
        help=f"Path to run-state file for dismissed ids (default: {_DEFAULT_STATE})",
    )
    replay_p.set_defaults(func=_cmd_replay)
    doctor_p = sub.add_parser(
        "doctor",
        help="Check the profile, credentials and every enabled source; "
        "exit 1 if anything is broken.",
    )
    doctor_p.add_argument(
        "--profile",
        default=str(_DEFAULT_PROFILE),
        metavar="PATH",
        help=f"Path to profile.yaml (default: {_DEFAULT_PROFILE})",
    )
    doctor_p.add_argument(
        "--state",
        default=str(_DEFAULT_STATE),
        metavar="PATH",
        help=f"Path to run-state file (default: {_DEFAULT_STATE})",
    )
    doctor_p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a table (for schedulers and CI).",
    )
    doctor_p.add_argument(
        "--offline",
        action="store_true",
        help="Skip the live source and board checks; validate config only.",
    )
    doctor_p.set_defaults(func=_cmd_doctor)

    probe_p = sub.add_parser(
        "probe",
        help="Detect ATS boards from a careers URL/slug, or check the profile watchlist.",
    )
    probe_p.add_argument(
        "target",
        nargs="?",
        metavar="URL_OR_SLUG",
        default=None,
        help="Careers page URL or bare company slug to probe.",
    )
    probe_p.add_argument(
        "--ats",
        choices=("greenhouse", "lever", "ashby", "workday"),
        default=None,
        help="ATS to probe (default: try greenhouse, lever, ashby in sequence).",
    )
    probe_p.add_argument(
        "--check",
        action="store_true",
        help="Probe every entry in the profile ats_watchlist.",
    )
    probe_p.add_argument(
        "--profile",
        default=str(_DEFAULT_PROFILE),
        metavar="PATH",
        help=f"Profile for --check mode (default: {_DEFAULT_PROFILE})",
    )
    probe_p.set_defaults(func=_cmd_probe)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
