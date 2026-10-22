"""Reference data for valuation: Damodaran's published industry tables, the company-to-industry map and the dated inputs (risk-free rate, equity risk premium, tax) the valuation code reads.

Kept in its own DuckDB file (`.athena/reference.duckdb`), apart from the warehouse the nightly build rewrites. Every row carries the date of the data it came from."""
