import sqlite3
from datetime import datetime


def main() -> None:
    conn = sqlite3.connect("data/notif_log.db")
    try:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM notifications")
        total_rows = cursor.fetchone()[0]
        print(f"Total rows: {total_rows}")

        cursor.execute(
            """
            SELECT id, app_name, interaction_type, response_time, timestamp_arrival
            FROM notifications
            ORDER BY timestamp_arrival DESC
            LIMIT 10
            """
        )
        rows = cursor.fetchall()

        print("Last 10 rows:")
        for row_id, app_name, interaction_type, response_time, timestamp_arrival in rows:
            readable_timestamp = datetime.fromtimestamp(timestamp_arrival)
            print(
                f"id={row_id}, app_name={app_name}, interaction_type={interaction_type}, "
                f"response_time={response_time}, timestamp_arrival={readable_timestamp}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()