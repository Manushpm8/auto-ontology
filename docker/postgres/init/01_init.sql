-- Seed databases that mirror the catalog previously held in Neo4j.
-- Run automatically by the postgres image on first startup.

CREATE DATABASE northwind_dw;
CREATE DATABASE ops_oltp;
CREATE DATABASE sales_lake;
CREATE DATABASE testdb;

-- ---------------------------------------------------------------------------
-- northwind_dw  (display name: "Northwind DW")
-- ---------------------------------------------------------------------------
\c northwind_dw

CREATE SCHEMA curated;
CREATE TABLE curated.table_1 (
    col_1 bigint,
    col_2 varchar(255),
    col_3 timestamp,
    col_4 boolean,
    col_5 double precision
);

CREATE SCHEMA mart;
CREATE TABLE mart.table_1 (
    col_1 bigint,
    col_2 varchar(255),
    col_3 timestamp,
    col_4 boolean,
    col_5 double precision
);

CREATE SCHEMA raw;
CREATE TABLE raw.table_1 (
    col_1 bigint,
    col_2 varchar(255),
    col_3 timestamp,
    col_4 boolean,
    col_5 double precision
);

-- ---------------------------------------------------------------------------
-- ops_oltp  (display name: "Ops OLTP")
-- ---------------------------------------------------------------------------
\c ops_oltp

CREATE TABLE public.table_1 (
    col_1 bigint,
    col_2 varchar(255),
    col_3 timestamp,
    col_4 boolean,
    col_5 double precision
);

-- ---------------------------------------------------------------------------
-- sales_lake  (display name: "Sales Lake")
-- ---------------------------------------------------------------------------
\c sales_lake

CREATE SCHEMA bronze;
CREATE TABLE bronze.table_1 (
    col_1 bigint,
    col_2 varchar(255),
    col_3 timestamp,
    col_4 boolean,
    col_5 double precision
);

CREATE SCHEMA silver;
CREATE TABLE silver.table_1 (
    col_1 bigint,
    col_2 varchar(255),
    col_3 timestamp,
    col_4 boolean,
    col_5 double precision
);

-- ---------------------------------------------------------------------------
-- testdb  (display name: "testdb")
-- ---------------------------------------------------------------------------
\c testdb

CREATE SCHEMA sales;

CREATE TABLE sales.customers (
    id         integer PRIMARY KEY,
    name       varchar(255),
    email      varchar(255),
    created_at timestamp
);

CREATE TABLE sales.orders (
    id          integer PRIMARY KEY,
    customer_id integer REFERENCES sales.customers(id),
    total       numeric(12, 2),
    status      varchar(32),
    ordered_at  timestamp
);
