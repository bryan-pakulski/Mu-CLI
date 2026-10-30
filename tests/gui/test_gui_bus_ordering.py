"""A turn's terminal event never overtakes the agent thread's last events.

The agent thread publishes through ``publish_threadsafe``; the route publishes
``turn_complete`` on the loop as soon as the turn's thread returns. Clients
must receive the agent's final deltas / ``assistant_end`` before
``turn_complete``, or the finished turn reopens as running ("thinking") until
the page is reloaded.
"""

import asyncio
import threading

from mu.gui.bus import EventBus


def test_turn_complete_arrives_after_agent_thread_events():
    async def scenario():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        queue = bus.subscribe("s")

        def agent_turn():
            bus.publish_threadsafe({"kind": "assistant_delta", "text": "done", "session_name": "s"})
            bus.publish_threadsafe({"kind": "assistant_end", "session_name": "s"})
            bus.publish_threadsafe({"kind": "turn_receipt", "session_name": "s"})

        thread = threading.Thread(target=agent_turn)
        thread.start()
        thread.join()
        await bus.publish({"kind": "turn_complete", "session_name": "s"})
        await asyncio.sleep(0)  # let any already-scheduled drain callback run

        kinds = []
        while not queue.empty():
            kinds.append(queue.get_nowait()["kind"])
        return kinds

    kinds = asyncio.run(scenario())
    assert kinds == ["assistant_delta", "assistant_end", "turn_receipt", "turn_complete"]


def test_threadsafe_events_still_publish_without_a_loop_side_publish():
    async def scenario():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        queue = bus.subscribe("s")
        threading.Thread(
            target=bus.publish_threadsafe,
            args=({"kind": "tool_call", "session_name": "s"},),
        ).start()
        event = await asyncio.wait_for(queue.get(), timeout=2)
        return event

    event = asyncio.run(scenario())
    assert event["kind"] == "tool_call"
    assert event["seq"] >= 1
