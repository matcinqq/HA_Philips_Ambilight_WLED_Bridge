from __future__ import annotations

from typing import Any

from .models import ColorValue


def extract_segment_colors(state: dict[str, Any], segment_ids: set[int]) -> dict[int, list[int] | None]:
    result: dict[int, list[int] | None] = {segment_id: None for segment_id in segment_ids}
    segments = state.get("seg", [])
    if not isinstance(segments, list):
        return result

    for segment in segments:
        if not isinstance(segment, dict):
            continue
        segment_id = segment.get("id")
        if not isinstance(segment_id, int) or segment_id not in segment_ids:
            continue
        colors = segment.get("col")
        if not isinstance(colors, list) or not colors:
            continue
        first_color = colors[0]
        if not isinstance(first_color, list):
            continue
        parsed: list[int] = []
        for channel in first_color:
            if isinstance(channel, bool) or not isinstance(channel, int):
                parsed = []
                break
            parsed.append(channel)
        if parsed:
            result[segment_id] = parsed
    return result


def compare_sent_to_readback(
    sent: dict[int, ColorValue],
    readback: dict[int, list[int] | None],
    *,
    include_white: bool,
) -> dict[str, dict[str, object]]:
    comparison: dict[str, dict[str, object]] = {}
    for segment_id, color in sorted(sent.items()):
        sent_color = color.as_list(include_white=include_white)
        read_color = readback.get(segment_id)
        comparison[str(segment_id)] = {
            "sent": sent_color,
            "read": read_color,
            "match": read_color == sent_color,
        }
    return comparison


def colors_to_json(colors: dict[int, ColorValue], *, include_white: bool) -> dict[str, list[int]]:
    return {
        str(segment_id): color.as_list(include_white=include_white)
        for segment_id, color in sorted(colors.items())
    }
