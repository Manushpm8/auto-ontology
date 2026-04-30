CREATE SCHEMA IF NOT EXISTS sales;

CREATE TABLE IF NOT EXISTS sales.customers (
    id         integer PRIMARY KEY,
    name       varchar(255),
    email      varchar(255),
    created_at timestamp
);

CREATE TABLE IF NOT EXISTS sales.orders (
    id          integer PRIMARY KEY,
    customer_id integer REFERENCES sales.customers(id),
    total       numeric(12, 2),
    status      varchar(32),
    ordered_at  timestamp
);
