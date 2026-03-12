import asyncio
from collections import defaultdict


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[session_id].add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        self._subscribers[session_id].discard(queue)

    async def publish(self, session_id: str, event: dict) -> None:
        for queue in self._subscribers[session_id]:
            await queue.put(event)


event_bus = EventBus()
