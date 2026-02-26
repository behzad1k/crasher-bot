import glob
import os
import sqlite3
from datetime import datetime


def combine_databases_preserve_structure(db_paths, output_db_path="combined.db"):
    """
    Combine multipliers and sessions tables from multiple databases into a single database
    while preserving the original table structure and maintaining timestamp order.

    Args:
        db_paths: List of paths to SQLite database files
        output_db_path: Path to save the combined database
    """

    # Remove output database if it already exists
    if os.path.exists(output_db_path):
        os.remove(output_db_path)
        print(f"Removed existing database: {output_db_path}")

    # Create new output database with the same structure
    conn_out = sqlite3.connect(output_db_path)
    cur_out = conn_out.cursor()

    # Create sessions table with the same structure
    cur_out.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_timestamp DATETIME NOT NULL,
            end_timestamp DATETIME,
            start_balance REAL,
            end_balance REAL,
            total_rounds INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Create multipliers table with the same structure
    cur_out.execute("""
        CREATE TABLE IF NOT EXISTS multipliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            multiplier REAL NOT NULL,
            bettor_count INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            session_id INTEGER,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
    """)

    # Create index on timestamp for better performance
    cur_out.execute("CREATE INDEX idx_multipliers_timestamp ON multipliers(timestamp)")
    cur_out.execute(
        "CREATE INDEX idx_sessions_start_timestamp ON sessions(start_timestamp)"
    )

    conn_out.commit()

    # Track ID mappings to maintain referential integrity
    session_id_map = {}  # Maps old session_id to new session_id
    all_sessions = []  # Store all sessions with source info for sorting
    all_multipliers = []  # Store all multipliers with source info for sorting

    # First pass: Collect all data from source databases
    for db_path in db_paths:
        if not os.path.exists(db_path):
            print(f"Warning: Database {db_path} does not exist. Skipping...")
            continue

        print(f"Reading data from: {db_path}")

        # Connect to source database
        conn_src = sqlite3.connect(db_path)
        conn_src.row_factory = sqlite3.Row
        cur_src = conn_src.cursor()

        try:
            # Read all sessions
            cur_src.execute("SELECT * FROM sessions ORDER BY start_timestamp")
            sessions = cur_src.fetchall()

            for session in sessions:
                session_dict = dict(session)
                session_dict["source_db"] = db_path
                session_dict["original_id"] = session_dict["id"]
                all_sessions.append(session_dict)

            # Read all multipliers
            cur_src.execute("SELECT * FROM multipliers ORDER BY timestamp")
            multipliers = cur_src.fetchall()

            for multiplier in multipliers:
                multiplier_dict = dict(multiplier)
                multiplier_dict["source_db"] = db_path
                multiplier_dict["original_id"] = multiplier_dict["id"]
                multiplier_dict["original_session_id"] = multiplier_dict["session_id"]
                all_multipliers.append(multiplier_dict)

        except sqlite3.Error as e:
            print(f"Error reading from {db_path}: {e}")
        finally:
            conn_src.close()

    # Sort sessions by start_timestamp
    all_sessions.sort(
        key=lambda x: x["start_timestamp"] if x["start_timestamp"] else datetime.min
    )

    print(f"\nInserting {len(all_sessions)} sessions in chronological order...")

    # Insert sessions in timestamp order
    for session in all_sessions:
        # Remove id and source fields before insertion
        session_data = {
            "start_timestamp": session["start_timestamp"],
            "end_timestamp": session["end_timestamp"],
            "start_balance": session["start_balance"],
            "end_balance": session["end_balance"],
            "total_rounds": session["total_rounds"],
            "created_at": session["created_at"],
        }

        cur_out.execute(
            """
            INSERT INTO sessions (
                start_timestamp, end_timestamp, start_balance,
                end_balance, total_rounds, created_at
            ) VALUES (
                :start_timestamp, :end_timestamp, :start_balance,
                :end_balance, :total_rounds, :created_at
            )
        """,
            session_data,
        )

        # Store the mapping from original session_id to new session_id
        new_session_id = cur_out.lastrowid
        session_id_map[(session["source_db"], session["original_id"])] = new_session_id

    conn_out.commit()

    # Sort multipliers by timestamp
    all_multipliers.sort(
        key=lambda x: x["timestamp"] if x["timestamp"] else datetime.min
    )

    print(f"Inserting {len(all_multipliers)} multipliers in chronological order...")

    # Insert multipliers in timestamp order with updated session_id references
    multipliers_inserted = 0
    for multiplier in all_multipliers:
        # Get the new session_id from the mapping
        new_session_id = session_id_map.get(
            (multiplier["source_db"], multiplier["original_session_id"]), None
        )

        # Remove id and source fields before insertion
        multiplier_data = {
            "multiplier": multiplier["multiplier"],
            "bettor_count": multiplier["bettor_count"],
            "timestamp": multiplier["timestamp"],
            "session_id": new_session_id,
        }

        cur_out.execute(
            """
            INSERT INTO multipliers (
                multiplier, bettor_count, timestamp, session_id
            ) VALUES (
                :multiplier, :bettor_count, :timestamp, :session_id
            )
        """,
            multiplier_data,
        )

        multipliers_inserted += 1

        # Commit every 1000 inserts for better performance
        if multipliers_inserted % 1000 == 0:
            conn_out.commit()
            print(f"  Inserted {multipliers_inserted} multipliers...")

    conn_out.commit()
    conn_out.close()

    print(f"\n{'=' * 60}")
    print(f"COMBINATION COMPLETE")
    print(f"{'=' * 60}")
    print(f"Output database: {output_db_path}")
    print(f"Sessions inserted: {len(all_sessions)}")
    print(f"Multipliers inserted: {len(all_multipliers)}")
    print(f"Source databases processed: {len(db_paths)}")


def verify_combined_database(db_path):
    """
    Verify the combined database by showing some statistics.
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    print(f"\n{'=' * 60}")
    print(f"VERIFICATION OF COMBINED DATABASE: {db_path}")
    print(f"{'=' * 60}")

    # Check sessions
    cur.execute("SELECT COUNT(*) FROM sessions")
    session_count = cur.fetchone()[0]

    cur.execute("""
        SELECT MIN(start_timestamp), MAX(start_timestamp)
        FROM sessions
    """)
    session_min, session_max = cur.fetchone()

    print(f"\nSessions table:")
    print(f"  Total records: {session_count}")
    print(f"  Date range: {session_min} to {session_max}")

    # Check multipliers
    cur.execute("SELECT COUNT(*) FROM multipliers")
    multiplier_count = cur.fetchone()[0]

    cur.execute("""
        SELECT MIN(timestamp), MAX(timestamp)
        FROM multipliers
    """)
    mult_min, mult_max = cur.fetchone()

    print(f"\nMultipliers table:")
    print(f"  Total records: {multiplier_count}")
    print(f"  Date range: {mult_min} to {mult_max}")

    # Check referential integrity
    cur.execute("""
        SELECT COUNT(*)
        FROM multipliers m
        LEFT JOIN sessions s ON m.session_id = s.id
        WHERE s.id IS NULL AND m.session_id IS NOT NULL
    """)
    orphaned = cur.fetchone()[0]

    if orphaned > 0:
        print(f"\nWARNING: {orphaned} multipliers have invalid session_id references")
    else:
        print(
            f"\nReferential integrity: All multipliers have valid session_id references"
        )

    # Show sample of earliest and latest multipliers
    print(f"\nEarliest multipliers (first 5):")
    cur.execute("""
        SELECT timestamp, multiplier, bettor_count, session_id
        FROM multipliers
        ORDER BY timestamp ASC
        LIMIT 5
    """)
    for row in cur.fetchall():
        print(
            f"  {row[0]} - Multiplier: {row[1]}, Bettors: {row[2]}, Session: {row[3]}"
        )

    print(f"\nLatest multipliers (last 5):")
    cur.execute("""
        SELECT timestamp, multiplier, bettor_count, session_id
        FROM multipliers
        ORDER BY timestamp DESC
        LIMIT 5
    """)
    for row in cur.fetchall():
        print(
            f"  {row[0]} - Multiplier: {row[1]}, Bettors: {row[2]}, Session: {row[3]}"
        )

    conn.close()


def preview_database_structure(db_path):
    """
    Preview the structure of a database.
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    print(f"\nDatabase structure for: {db_path}")
    print("-" * 40)

    # Get table info
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cur.fetchall()

    for table in tables:
        table_name = table[0]
        print(f"\nTable: {table_name}")

        # Get column info
        cur.execute(f"PRAGMA table_info({table_name})")
        columns = cur.fetchall()
        for col in columns:
            print(f"  {col[1]} ({col[2]})")

    conn.close()


# Example usage
if __name__ == "__main__":
    # Method 1: Specify database paths manually
    db_files = ["crasher_data_36k.db", "crasher_data_47k.db"]

    # Check if files exist and show their structure
    print("Source databases found:")
    for db_file in db_files:
        if os.path.exists(db_file):
            print(f"  ✓ {db_file}")
            preview_database_structure(db_file)
        else:
            print(f"  ✗ {db_file} (not found)")

    # Combine all databases
    output_db = "combined_data.db"
    combine_databases_preserve_structure(db_files, output_db)

    # Verify the combined database
    verify_combined_database(output_db)

    print(f"\nYou can now query the combined database: {output_db}")
    print("Example queries:")
    print(
        '  sqlite3 combined_data.db "SELECT * FROM multipliers ORDER BY timestamp LIMIT 10;"'
    )
    print(
        '  sqlite3 combined_data.db "SELECT * FROM sessions ORDER BY start_timestamp LIMIT 5;"'
    )
