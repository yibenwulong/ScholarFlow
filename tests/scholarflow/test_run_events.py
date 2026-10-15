from concurrent.futures import ThreadPoolExecutor

from .test_jobs import job_context


def test_reconnect_reads_only_events_after_last_seen_sequence(job_context):
    store, run = job_context
    for number in range(3):
        store.append_event(run.id, 'progress', 'read', {'count': number})
    assert [event.seq for event in store.events(run.id, after=1)] == [2, 3]


def test_parallel_event_writers_do_not_lose_or_duplicate_sequences(job_context):
    store, run = job_context
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda value: store.append_event(run.id, 'progress', 'read', {'count': value}), range(12)))
    assert [event.seq for event in store.events(run.id)] == list(range(1, 13))
