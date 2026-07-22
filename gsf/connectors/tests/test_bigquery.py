# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from gsf.connectors.bigquery import _parse_connection_string
from gsf.connectors.connection_string_factory import build_connection_string
from gsf.connectors.snowflake import _parse_connection_string as parse_snowflake


def test_parse_logical_form_matches_sqlite_stem_routing() -> None:
    """Host is the routing name (like a SQLite file stem / Spider2 db_id)."""
    name, datasets, billing, creds, location = _parse_connection_string(
        "bigquery://ga4"
        "?datasets=bigquery-public-data.ga4_obfuscated_sample_ecommerce"
        "&billing_project=my-gcp"
    )

    assert name == "ga4"
    assert datasets == [("bigquery-public-data", "ga4_obfuscated_sample_ecommerce")]
    assert billing == "my-gcp"
    assert creds is None
    assert location is None


def test_parse_multiple_physical_datasets_under_one_logical_db() -> None:
    name, datasets, billing, _creds, _location = _parse_connection_string(
        "bigquery://austin"
        "?datasets=bigquery-public-data.austin_311,bigquery-public-data.austin_crime"
        "&billing_project=my-gcp"
    )

    assert name == "austin"
    assert datasets == [
        ("bigquery-public-data", "austin_311"),
        ("bigquery-public-data", "austin_crime"),
    ]
    assert billing == "my-gcp"


def test_parse_physical_shorthand_uses_dataset_as_name() -> None:
    name, datasets, billing, creds, location = _parse_connection_string(
        "bigquery://bigquery-public-data/ga4_obfuscated_sample_ecommerce"
    )

    assert name == "ga4_obfuscated_sample_ecommerce"
    assert datasets == [("bigquery-public-data", "ga4_obfuscated_sample_ecommerce")]
    assert billing == "bigquery-public-data"
    assert creds is None
    assert location is None


def test_parse_credentials_and_location() -> None:
    name, datasets, billing, creds, location = _parse_connection_string(
        "bigquery://ga4"
        "?datasets=bigquery-public-data.ga4_obfuscated_sample_ecommerce"
        "&billing_project=my-gcp"
        "&credentials=%2Ftmp%2Fkey.json"
        "&location=US"
    )

    assert name == "ga4"
    assert datasets == [("bigquery-public-data", "ga4_obfuscated_sample_ecommerce")]
    assert billing == "my-gcp"
    assert creds == "/tmp/key.json"
    assert location == "US"


def test_build_connection_string_bigquery_logical() -> None:
    url = build_connection_string(
        {
            "type": "bigquery",
            "name": "austin",
            "datasets": [
                "bigquery-public-data.austin_311",
                "bigquery-public-data.austin_crime",
            ],
            "billing_project": "my-gcp",
        }
    )

    assert url.startswith("bigquery://austin?")
    assert "datasets=bigquery-public-data.austin_311%2Cbigquery-public-data.austin_crime" in url
    assert "billing_project=my-gcp" in url


def test_snowflake_and_bigquery_multi_db_names_are_distinct() -> None:
    """Same multi-DB pattern as SQLite: one URL → one unique database_name."""
    sf_patents = parse_snowflake(
        "snowflake://u:p@acct?warehouse=WH&database=PATENTS"
    )[3]
    sf_repos = parse_snowflake(
        "snowflake://u:p@acct?warehouse=WH&database=GITHUB_REPOS"
    )[3]
    bq_ga4 = _parse_connection_string(
        "bigquery://ga4?datasets=bigquery-public-data.ga4_obfuscated_sample_ecommerce"
    )[0]
    bq_austin = _parse_connection_string(
        "bigquery://austin"
        "?datasets=bigquery-public-data.austin_311,bigquery-public-data.austin_crime"
    )[0]

    names = [sf_patents, sf_repos, bq_ga4, bq_austin]
    assert names == ["PATENTS", "GITHUB_REPOS", "ga4", "austin"]
    assert len(set(names)) == 4
