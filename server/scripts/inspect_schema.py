from __future__ import annotations

from sqlalchemy import text

from app.db.session import engine


def main() -> None:
    with engine.connect() as conn:
        table_rows = conn.execute(
            text(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY table_schema, table_name
                """
            )
        )
        extension_rows = conn.execute(
            text(
                """
                SELECT extname
                FROM pg_extension
                WHERE extname IN ('pgcrypto', 'vector')
                ORDER BY extname
                """
            )
        )

        print("Tables:")
        for row in table_rows:
            print(f"{row.table_schema}.{row.table_name}")

        print("Extensions:")
        for row in extension_rows:
            print(row.extname)


if __name__ == "__main__":
    main()
