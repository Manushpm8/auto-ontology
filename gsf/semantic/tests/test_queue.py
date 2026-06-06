"""Tests for semantic layer BFS queue."""

from __future__ import annotations

import heapq

from gsf.semantic.queue import QueueEntry, QueuePriority, TablesQueue


def test_queue_priority_ordering() -> None:
    entries = [
        QueueEntry(
            priority=int(QueuePriority.VDB), hop=1, table_id="3", table_name="c"
        ),
        QueueEntry(
            priority=int(QueuePriority.JOIN), hop=1, table_id="1", table_name="a"
        ),
        QueueEntry(priority=int(QueuePriority.FK), hop=1, table_id="2", table_name="b"),
    ]
    heapq.heapify(entries)
    order = [heapq.heappop(entries).table_name for _ in range(3)]
    assert order == ["a", "b", "c"]


def test_tables_queue_deduplicates() -> None:
    tables_by_name = {
        "orders": {"id": "t1", "name": "orders"},
        "customers": {"id": "t2", "name": "customers"},
    }
    queue = TablesQueue(tables_by_name, join_edges=[])
    queue.push("orders", QueuePriority.FK, hop=0, source="seed")
    queue.push("orders", QueuePriority.JOIN, hop=0, source="join")
    first = queue.pop()
    second = queue.pop()
    assert first is not None
    assert first.table_name == "orders"
    assert second is None
