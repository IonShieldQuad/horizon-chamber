"""Reusable SSE (Server-Sent Events) helper for Horizon Chamber.

Provides helpers for formatting SSE events and wrapping async generators
as StreamingResponse instances.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Union

from starlette.responses import StreamingResponse


def sse_event(event: str, data: Union[dict, str]) -> str:
    """Format a single SSE event string.

    Args:
        event: The event type (e.g. 'activity', 'sunrise').
        data: The payload — either a dict (serialized to JSON) or a plain string.

    Returns:
        A properly formatted SSE message string.
    """
    if isinstance(data, dict):
        data = json.dumps(data)
    return f"event: {event}\ndata: {data}\n\n"


async def _sse_generator(
    source: AsyncIterator[tuple[str, Any]],
    *,
    ping_interval: float = 15.0,
) -> AsyncIterator[str]:
    """Internal generator that yields SSE-formatted messages from an
    async iterator of (event, data) tuples, with keep-alive pings.

    Uses short polling intervals so that the generator can be cancelled
    promptly on Windows (where anyio task-group cancellation does not
    propagate reliably through long asyncio.wait_for calls).

    Args:
        source: Async iterator yielding (event_name, payload) tuples.
        ping_interval: Seconds between keep-alive comment lines.

    Yields:
        SSE-formatted strings.
    """
    it = source.__aiter__()
    poll_interval = min(ping_interval, 0.5)
    elapsed = 0.0

    try:
        while True:
            try:
                event_name, data = await asyncio.wait_for(
                    it.__anext__(), timeout=poll_interval
                )
                elapsed = 0.0
                yield sse_event(event_name, data)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                elapsed += poll_interval
                if elapsed >= ping_interval:
                    yield ": ping\n\n"
                    elapsed = 0.0
                continue
    finally:
        # Always clean up the source iterator, regardless of how we exit
        # (client disconnect, cancellation, error, or normal completion)
        await it.aclose()


def sse_response(
    source: AsyncIterator[tuple[str, Any]],
    *,
    ping_interval: float = 15.0,
) -> StreamingResponse:
    """Wrap an async generator of (event, data) tuples in an SSE
    StreamingResponse with automatic keep-alive pings.

    Args:
        source: Async iterator yielding (event_name, payload) tuples.
        ping_interval: Seconds between keep-alive pings (default 15s).

    Returns:
        A StreamingResponse with media_type 'text/event-stream'.
    """
    return StreamingResponse(
        _sse_generator(source, ping_interval=ping_interval),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
