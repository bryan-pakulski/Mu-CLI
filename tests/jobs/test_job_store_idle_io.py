"""The job controller polls its store every second; idle polls must not rewrite files on disk."""
import os

from mu.jobs.store import JobStore


def test_idle_job_store_reads_keep_the_wal_files_in_place(tmp_path):
    store = JobStore(str(tmp_path / "jobs.sqlite3"))
    wal, shm = store.path + "-wal", store.path + "-shm"
    with store._transaction() as conn:
        conn.execute("SELECT count(*) FROM jobs").fetchall()
    before = (os.stat(wal).st_ino, os.stat(shm).st_ino)
    for _ in range(5):
        with store._transaction() as conn:
            conn.execute("SELECT count(*) FROM jobs").fetchall()
    assert (os.stat(wal).st_ino, os.stat(shm).st_ino) == before
