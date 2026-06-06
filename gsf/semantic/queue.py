"""Priority BFS queue for table expansion."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any


class QueuePriority(IntEnum):
    """Lower value = higher priority."""

    JOIN = 1
    FK = 2
    VDB = 3


class EntryKind(str, Enum):
    EXPAND = "expand"
    FINALIZE = "finalize"


@dataclass(order=True)
class QueueEntry:
    priority: int
    hop: int
    table_id: str = field(compare=False)
    table_name: str = field(compare=False)
    kind: EntryKind = field(compare=False, default=EntryKind.EXPAND)
    source: str = field(compare=False, default="")


class TablesQueue:
    """Min-heap queue for BFS table expansion (expand entries only)."""

    def __init__(
        self,
        tables_by_name: dict[str, dict[str, Any]],
        join_edges: list[dict[str, Any]] | None = None,
    ) -> None:
        self.tables_by_name = tables_by_name
        self._heap: list[QueueEntry] = []
        self._seen: set[str] = set()

        self._join_neighbors: dict[str, list[tuple[str, str]]] = {}
        for edge in join_edges or []:
            src_name = edge["source_table"]
            tgt_name = edge["target_table"]
            src_id = edge.get("source_table_id") or ""
            tgt_id = edge.get("target_table_id") or ""
            if src_name in tables_by_name:
                tid = tables_by_name[src_name]["id"]
                self._join_neighbors.setdefault(src_name, []).append((tgt_name, tid))
            if tgt_name in tables_by_name:
                tid = tables_by_name[tgt_name]["id"]
                self._join_neighbors.setdefault(tgt_name, []).append((src_name, tid))
            _ = src_id, tgt_id

    def push(
        self,
        table_name: str,
        priority: QueuePriority,
        *,
        hop: int,
        source: str,
    ) -> None:
        table = self.tables_by_name.get(table_name)
        if not table or table["id"] in self._seen:
            return
        entry = QueueEntry(
            priority=int(priority),
            hop=hop,
            table_id=table["id"],
            table_name=table_name,
            kind=EntryKind.EXPAND,
            source=source,
        )
        heapq.heappush(self._heap, entry)

    def push_seed(self, table_name: str) -> None:
        self.push(table_name, QueuePriority.FK, hop=0, source="seed")

    def pop(self) -> QueueEntry | None:
        while self._heap:
            entry = heapq.heappop(self._heap)
            if entry.table_id in self._seen:
                continue
            self._seen.add(entry.table_id)
            return entry
        return None

    def discover_neighbors(
        self,
        table_id: str,
        table_name: str,
        hop: int,
        *,
        vdb_table_names: list[str] | None = None,
        fk_targets: list[str] | None = None,
    ) -> None:
        """Enqueue JOIN, FK, and VDB-discovered tables."""
        for neighbor_name, _ in self._join_neighbors.get(table_name, []):
            if neighbor_name in self.tables_by_name:
                self.push(neighbor_name, QueuePriority.JOIN, hop=hop + 1, source="join")

        for tgt in fk_targets or []:
            if tgt in self.tables_by_name:
                self.push(tgt, QueuePriority.FK, hop=hop + 1, source="fk")

        for name in vdb_table_names or []:
            if name in self.tables_by_name:
                self.push(name, QueuePriority.VDB, hop=hop + 1, source="vdb")
