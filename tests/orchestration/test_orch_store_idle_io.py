"""An idle master polls its store constantly; those reads must not rewrite files on disk."""
import os

from mu.orchestration.store import OrchestrationStore


def test_idle_store_reads_keep_the_wal_files_in_place(tmp_path):
    store = OrchestrationStore(str(tmp_path / "orch"))
    wal, shm = store.path + "-wal", store.path + "-shm"
    with store.conn() as conn:
        conn.execute("SELECT count(*) FROM nodes").fetchall()
    before = (os.stat(wal).st_ino, os.stat(shm).st_ino)
    for _ in range(5):
        with store.conn() as conn:
            conn.execute("SELECT count(*) FROM nodes").fetchall()
        with store.tx() as conn:
            conn.execute("SELECT count(*) FROM nodes").fetchall()
    # A checkpoint-and-delete on every close would recreate both files each poll.
    assert (os.stat(wal).st_ino, os.stat(shm).st_ino) == before
