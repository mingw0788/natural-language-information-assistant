from info_assistant.storage.database import Database


def test_migration_is_repeatable_and_enables_foreign_keys(tmp_path) -> None:
    database = Database(tmp_path / "assistant.sqlite3")

    database.migrate()
    database.migrate()

    with database.connection() as connection:
        migrations = connection.execute("SELECT version FROM schema_migrations").fetchall()
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert [row[0] for row in migrations] == [1, 2, 3, 4, 5, 6, 7]
    assert foreign_keys == 1
