"""In-process fan-out queues for Core WebSocket clients."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator
from typing import Any


class EventHub:
    def __init__(self) -> None:
        self._subscribers: dict[str, dict[int, asyncio.Queue[dict[str, Any]]]] = {
            "client": {},
            "mirror": {},
        }
        self._ids = itertools.count(1)

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers.get(channel, {}).values()):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(message)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]:
        subscriber_id = next(self._ids)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers[channel][subscriber_id] = queue
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers[channel].pop(subscriber_id, None)
