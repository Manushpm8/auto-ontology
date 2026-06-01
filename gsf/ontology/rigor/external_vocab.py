"""External ontology vocabulary sources for the Rigor pipeline.

Sources are selected via a domain mapping config. All sources implement
the VocabSource protocol and return ExternalConcept results.

- LOVClient: Linked Open Vocabularies (all domains, no key)
- BioPortalClient: biomedical ontologies (requires BIOPORTAL_API_KEY)
- FIBOLocalIndex: Financial Industry Business Ontology (local OWL)
- BirdEvidenceKB: BIRD dataset evidence strings as domain knowledge
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------


class ExternalConcept(BaseModel):
    """A concept found in an external ontology."""

    label: str = Field(..., description="Preferred label / name.")
    uri: str = Field("", description="Ontology URI if available.")
    definition: str = Field("", description="Short definition.")
    source: str = Field(
        ..., description="Which source returned this, e.g. 'LOV', 'BioPortal'."
    )
    vocabulary: str = Field(
        "", description="Specific vocabulary, e.g. 'schema.org', 'FIBO-FBC'."
    )


class ExternalMatch(BaseModel):
    """Aggregated external knowledge for a table/column."""

    term: str = Field(..., description="The search term used.")
    concepts: list[ExternalConcept] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# VocabSource protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class VocabSource(Protocol):
    def search(self, term: str, max_results: int = 5) -> list[ExternalConcept]: ...


# ---------------------------------------------------------------------------
# LOV (Linked Open Vocabularies) — always active, no key
# ---------------------------------------------------------------------------

_LOV_SEARCH_URL = "https://lov.linkeddata.es/dataset/lov/api/v2/term/search"


_LOV_FAIL_THRESHOLD = 3  # disable after this many consecutive failures


class LOVClient:
    """Search LOV for ontology classes matching a term.

    Tracks consecutive failures and disables itself after
    ``_LOV_FAIL_THRESHOLD`` to avoid wasting time on a dead API.
    """

    def __init__(self) -> None:
        self._consecutive_failures = 0
        self._disabled = False

    def search(self, term: str, max_results: int = 5) -> list[ExternalConcept]:
        if self._disabled:
            return []

        try:
            resp = httpx.get(
                _LOV_SEARCH_URL,
                params={"q": term, "type": "class", "page_size": max_results},
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            self._consecutive_failures = 0
        except Exception:
            self._consecutive_failures += 1
            if self._consecutive_failures >= _LOV_FAIL_THRESHOLD:
                self._disabled = True
                logger.warning(
                    "LOV disabled after %d consecutive failures — "
                    "API appears down, skipping future requests",
                    self._consecutive_failures,
                )
            else:
                logger.debug("LOV search failed for %r", term, exc_info=True)
            return []

        results: list[ExternalConcept] = []
        for hit in data.get("results", [])[:max_results]:
            highlight = hit.get("highlight", {})
            label = ""
            for key in ("prefixedName", "localName", "uri"):
                vals = highlight.get(key, [])
                if vals:
                    label = _strip_html(vals[0])
                    break
            if not label:
                label = (
                    hit.get("prefixedName", [""])[0] if hit.get("prefixedName") else ""
                )

            vocab_prefix = ""
            vocab_info = hit.get("vocabulary.prefix", [])
            if vocab_info:
                vocab_prefix = (
                    vocab_info[0] if isinstance(vocab_info, list) else str(vocab_info)
                )

            results.append(
                ExternalConcept(
                    label=label,
                    uri=hit.get("uri", [""])[0]
                    if isinstance(hit.get("uri"), list)
                    else str(hit.get("uri", "")),
                    source="LOV",
                    vocabulary=vocab_prefix,
                )
            )
        return results


# ---------------------------------------------------------------------------
# BioPortal — biomedical, requires API key
# ---------------------------------------------------------------------------

_BIOPORTAL_SEARCH_URL = "https://data.bioontology.org/search"


class BioPortalClient:
    """Search BioPortal for ontology concepts."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("BIOPORTAL_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def search(self, term: str, max_results: int = 5) -> list[ExternalConcept]:
        if not self.available:
            return []
        try:
            resp = httpx.get(
                _BIOPORTAL_SEARCH_URL,
                params={
                    "q": term,
                    "pagesize": max_results,
                    "include": "prefLabel,definition",
                },
                headers={"Authorization": f"apikey token={self._api_key}"},
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.debug("BioPortal search failed for %r", term, exc_info=True)
            return []

        results: list[ExternalConcept] = []
        for item in data.get("collection", [])[:max_results]:
            definition = ""
            defs = item.get("definition", [])
            if defs and isinstance(defs, list):
                definition = defs[0]

            ontology_link = item.get("links", {}).get("ontology", "")
            ont_name = ontology_link.rsplit("/", 1)[-1] if ontology_link else ""

            results.append(
                ExternalConcept(
                    label=item.get("prefLabel", ""),
                    uri=item.get("@id", ""),
                    definition=definition,
                    source="BioPortal",
                    vocabulary=ont_name,
                )
            )
        return results


# ---------------------------------------------------------------------------
# FIBO Local Index — finance, parsed from OWL
# ---------------------------------------------------------------------------


class FIBOLocalIndex:
    """Search FIBO concepts from a locally cached OWL file.

    Downloads FIBO OWL modules from GitHub via sparse checkout on first
    use and builds an in-memory label index. Uses rdflib for parsing.
    """

    _FIBO_REPO = "https://github.com/edmcouncil/fibo.git"
    _FIBO_MODULES = ["FND", "FBC", "BE"]
    _CACHE_DIR = ".cache/fibo"

    def __init__(self) -> None:
        self._labels: dict[str, str] | None = None

    def _clone_sparse(self, repo_dir: Path) -> None:
        import subprocess  # noqa: PLC0415

        logger.info("Cloning FIBO (sparse) into %s ...", repo_dir)
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--sparse",
                self._FIBO_REPO,
                str(repo_dir),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "sparse-checkout", "set", *self._FIBO_MODULES],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )

    def _ensure_loaded(self) -> dict[str, str]:
        if self._labels is not None:
            return self._labels

        cache_dir = _project_root() / self._CACHE_DIR
        repo_dir = cache_dir / "repo"

        if not (repo_dir / ".git").exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            try:
                self._clone_sparse(repo_dir)
            except Exception:
                logger.warning("Failed to clone FIBO repo", exc_info=True)
                self._labels = {}
                return self._labels

        self._labels = {}
        try:
            import rdflib  # noqa: PLC0415

            g = rdflib.Graph()
            parsed = 0
            for rdf_file in repo_dir.rglob("*.rdf"):
                if rdf_file.name.startswith("All") or "Metadata" in rdf_file.name:
                    continue
                try:
                    g.parse(str(rdf_file), format="xml")
                    parsed += 1
                except Exception:
                    logger.debug("Skipped unparseable %s", rdf_file.name)

            rdfs_label = rdflib.RDFS.label
            for _, _, obj in g.triples((None, rdfs_label, None)):
                self._labels[str(obj).lower()] = str(obj)
            logger.info(
                "FIBO index loaded: %d labels from %d files", len(self._labels), parsed
            )
        except Exception:
            logger.warning("Failed to parse FIBO files", exc_info=True)
            self._labels = {}

        return self._labels

    def search(self, term: str, max_results: int = 5) -> list[ExternalConcept]:
        labels = self._ensure_loaded()
        if not labels:
            return []

        term_lower = term.lower()
        matches: list[ExternalConcept] = []
        for key, original_label in labels.items():
            if term_lower in key:
                matches.append(
                    ExternalConcept(
                        label=original_label,
                        source="FIBO",
                        vocabulary="FIBO",
                    )
                )
                if len(matches) >= max_results:
                    break
        return matches


# ---------------------------------------------------------------------------
# BIRD Evidence KB
# ---------------------------------------------------------------------------


class BirdEvidenceKB:
    """Uses BIRD evidence strings as domain knowledge."""

    def __init__(self, evidence: dict[str, list[str]]) -> None:
        self._evidence = evidence.get("all", [])

    def get_evidence_for_table(
        self,
        table_name: str,
        column_names: list[str] | None = None,
    ) -> list[str]:
        """Return evidence strings relevant to this table."""
        search_terms = {table_name.lower()}
        if column_names:
            search_terms.update(c.lower() for c in column_names)

        return [
            ev for ev in self._evidence if any(t in ev.lower() for t in search_terms)
        ]


# ---------------------------------------------------------------------------
# Domain mapping config
# ---------------------------------------------------------------------------

DOMAIN_MAP: dict[str, list[str]] = {
    "financial": ["LOV", "FIBO"],
    "debit_card_specializing": ["LOV", "FIBO"],
    "thrombosis_prediction": ["LOV", "BioPortal"],
    "toxicology": ["LOV", "BioPortal"],
    "formula_1": ["LOV"],
    "european_football_2": ["LOV"],
    "california_schools": ["LOV"],
    "student_club": ["LOV"],
    "card_games": ["LOV"],
    "superhero": ["LOV"],
    "codebase_community": ["LOV"],
}

_DEFAULT_SOURCES = ["LOV"]


# ---------------------------------------------------------------------------
# ExternalVocabService — aggregates all sources
# ---------------------------------------------------------------------------


class ExternalVocabService:
    """Domain-aware external vocabulary service.

    Instantiates the right sources for a given db_id and aggregates results.
    """

    def __init__(
        self,
        db_id: str,
        evidence: dict[str, list[str]] | None = None,
    ) -> None:
        self._db_id = db_id
        self._evidence_kb = BirdEvidenceKB(evidence or {})

        source_names = DOMAIN_MAP.get(db_id, _DEFAULT_SOURCES)
        self._sources: list[VocabSource] = []

        for name in source_names:
            if name == "LOV":
                self._sources.append(LOVClient())
            elif name == "BioPortal":
                client = BioPortalClient()
                if client.available:
                    self._sources.append(client)
                else:
                    logger.info(
                        "BioPortal skipped (no BIOPORTAL_API_KEY) for %s",
                        db_id,
                    )
            elif name == "FIBO":
                self._sources.append(FIBOLocalIndex())

        logger.info(
            "ExternalVocabService for %s: %s",
            db_id,
            [type(s).__name__ for s in self._sources],
        )

    def find_similar_terms(
        self,
        table_name: str,
        columns: list[dict[str, Any]],
        max_per_source: int = 3,
    ) -> list[ExternalMatch]:
        """Search all active sources for terms related to this table."""
        col_names = [c.get("name", "") for c in columns if c.get("name")]
        search_terms = [table_name] + col_names[:5]

        matches: list[ExternalMatch] = []
        for term in search_terms:
            all_concepts: list[ExternalConcept] = []
            for source in self._sources:
                try:
                    results = source.search(term, max_results=max_per_source)
                    all_concepts.extend(results)
                except Exception:
                    logger.debug(
                        "Source %s failed for %r",
                        type(source).__name__,
                        term,
                        exc_info=True,
                    )
            if all_concepts:
                matches.append(ExternalMatch(term=term, concepts=all_concepts))

        return matches

    def get_evidence_for_table(
        self,
        table_name: str,
        column_names: list[str] | None = None,
    ) -> list[str]:
        return self._evidence_kb.get_evidence_for_table(table_name, column_names)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_html(text: str) -> str:
    """Remove simple HTML tags from LOV highlight responses."""
    import re  # noqa: PLC0415

    return re.sub(r"<[^>]+>", "", text)


def _project_root() -> Path:
    """Return the GSF project root (two levels up from this file)."""
    return Path(__file__).resolve().parents[3]
