from dataclasses import dataclass

from kumoapi.typing import Stype

from gsf.retrieval.kumo import predictor
from gsf.retrieval.kumo.predictor import (
    _deduplicate_inferred_links,
    _retype_text_columns,
)


@dataclass(frozen=True)
class _Column:
    name: str


@dataclass(frozen=True)
class _Table:
    primary_key: _Column


@dataclass(frozen=True)
class _Edge:
    src_table: str
    fkey: str
    dst_table: str


class _Graph:
    def __init__(self) -> None:
        self.edges = [
            _Edge("JOB_OUTCOMES", "restart_of_job_id", "JOBS"),
            _Edge("JOB_OUTCOMES", "job_id", "JOBS"),
            _Edge("JOBS", "project_id", "PROJECTS"),
        ]
        self.tables = {
            "JOBS": _Table(_Column("job_id")),
            "PROJECTS": _Table(_Column("project_id")),
        }

    def __getitem__(self, table: str) -> _Table:
        return self.tables[table]

    def unlink(self, src_table: str, fkey: str, dst_table: str) -> None:
        self.edges.remove(_Edge(src_table, fkey, dst_table))


def test_deduplicate_inferred_links_prefers_destination_primary_key_name() -> None:
    graph = _Graph()

    assert _deduplicate_inferred_links(graph) == 1
    assert graph.edges == [
        _Edge("JOB_OUTCOMES", "job_id", "JOBS"),
        _Edge("JOBS", "project_id", "PROJECTS"),
    ]


class _StypedColumn:
    """Mutable column stub: the real SDK column exposes a settable ``stype``."""

    def __init__(self, name: str, stype) -> None:
        self.name = name
        self.stype = stype


class _StypedTable:
    def __init__(self, columns: list[_StypedColumn]) -> None:
        self.columns = columns


class _StypedGraph:
    def __init__(self, tables: dict[str, _StypedTable]) -> None:
        self.tables = tables


def _graph_with_text_column() -> _StypedGraph:
    return _StypedGraph(
        {
            "GPUS": _StypedTable(
                [
                    _StypedColumn("SERIAL_NUMBER", Stype.text),
                    _StypedColumn("GPU_ID", Stype.ID),
                    _StypedColumn("GPU_AGE_DAYS", Stype.numerical),
                ]
            )
        }
    )


def test_retype_text_columns_rewrites_only_text() -> None:
    """A single text column otherwise fails EVERY prediction on the graph: the sampler
    ships it as a list of words while the payload declares its dtype as 'string'."""
    graph = _graph_with_text_column()

    assert _retype_text_columns(graph) == ["GPUS.SERIAL_NUMBER"]

    stypes = {c.name: c.stype for c in graph.tables["GPUS"].columns}
    assert stypes["SERIAL_NUMBER"] == Stype.categorical
    # Untouched: re-typing anything else would change features for no reason.
    assert stypes["GPU_ID"] == Stype.ID
    assert stypes["GPU_AGE_DAYS"] == Stype.numerical


def test_retype_text_columns_is_a_noop_without_text_columns() -> None:
    graph = _StypedGraph({"GPUS": _StypedTable([_StypedColumn("GPU_ID", Stype.ID)])})
    assert _retype_text_columns(graph) == []


def test_retype_text_columns_can_be_disabled(monkeypatch) -> None:
    """Escape hatch for a deployment that declares list dtypes correctly."""
    monkeypatch.setattr(predictor, "_ALLOW_TEXT_STYPE", True)
    graph = _graph_with_text_column()

    assert _retype_text_columns(graph) == []
    assert graph.tables["GPUS"].columns[0].stype == Stype.text
