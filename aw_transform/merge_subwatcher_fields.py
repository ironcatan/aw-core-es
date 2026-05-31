import logging
from copy import deepcopy
from typing import List, Optional

from aw_core.models import Event

from .filter_period_intersect import _get_event_period

logger = logging.getLogger(__name__)


def merge_subwatcher_fields(
    base_events: List[Event],
    subwatcher_events: List[Event],
    keys: List[str],
    conflict: str = "base_wins",
) -> List[Event]:
    """
    For each event in *base_events*, find the longest-overlapping event in
    *subwatcher_events* and copy the named *keys* from that subwatcher event
    into the base event's ``data`` dict.

    Timestamps, durations, and event count of *base_events* are **unchanged**
    — no phantom events are created. This makes duration/app/title aggregations
    stay correct, unlike the ``concat`` workaround.

    This is the backend primitive that lets every client (webui, native UIs,
    exporters) categorize by subwatcher fields (browser ``url``/``$domain``;
    editor ``project``/``file``/``language``) without bespoke per-watcher
    client-side code.

    Args:
        base_events: The canonical window/afk-filtered stream to enrich.
        subwatcher_events: Events from a subwatcher bucket (e.g. aw-watcher-vim,
            aw-watcher-web).  Should already be clipped via
            ``filter_period_intersect`` before passing here.
        keys: Which keys to copy from the subwatcher event into the base event.
            Keys already present in the base event are left untouched when
            ``conflict="base_wins"`` (default).
        conflict: ``"base_wins"`` (default) — base event's existing keys are
            never overwritten; subwatcher fields are purely additive.
            ``"sub_wins"`` — subwatcher fields overwrite base fields.

    Returns:
        A new list of base events with subwatcher fields injected.  Events in
        *base_events* that have no overlapping subwatcher event are returned
        with their original data unchanged.

    Example::

        window_events = query_bucket(bid_window)
        editor_events = flood(query_bucket(bid_editor))
        editor_events = filter_period_intersect(editor_events, window_events)
        window_events = merge_subwatcher_fields(
            window_events, editor_events, ["project", "file", "language"]
        )
        # Now categorize(window_events, ...) can match on "project"/"file"

    Note on N:1 overlap:
        When multiple subwatcher events overlap a single base event, the one
        with the **longest overlap duration** is used (attach-longest strategy).
        This matches heartbeat granularity and avoids splitting base events.
    """
    if not subwatcher_events or not keys:
        return base_events

    # Build a sorted copy so we can do a linear scan
    sub_sorted = sorted(subwatcher_events, key=lambda e: e.timestamp)

    result: List[Event] = []
    for base in base_events:
        base_period = _get_event_period(base)
        best_sub: Optional[Event] = None
        best_overlap_secs: float = 0.0

        for sub in sub_sorted:
            sub_period = _get_event_period(sub)
            # Once sub starts after base ends we can stop
            if sub_period.start >= base_period.end:
                break
            # Skip sub events that end before base starts
            if sub_period.end <= base_period.start:
                continue
            ip = base_period.intersection(sub_period)
            if ip:
                overlap_secs = ip.duration.total_seconds()
                if overlap_secs > best_overlap_secs:
                    best_overlap_secs = overlap_secs
                    best_sub = sub

        enriched = deepcopy(base)
        if best_sub is not None:
            for key in keys:
                if key in best_sub.data:
                    if conflict == "base_wins" and key in enriched.data:
                        pass  # base keeps its value
                    else:
                        enriched.data[key] = best_sub.data[key]
        result.append(enriched)

    return result
