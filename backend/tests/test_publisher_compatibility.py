"""Database admission capability is required even on an older connection."""
import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.exc import DBAPIError
from backend.app.db import SessionLocal,engine
from backend.app.models import QueueItem


@pytest.mark.parametrize('state',['ready','retry','posting'])
def test_unmarked_database_connection_cannot_write_posting_status(state):
    with SessionLocal() as s:
        item=QueueItem(title='Synthetic compatibility protocol',status=state,accounts=['default:youtube'])
        s.add(item);s.commit();qid=item.id
    # An independently created connection has no new-runtime capability marker.
    # No external service or credential is used by this SQL-only old-worker lane.
    old=create_engine(engine.url)
    try:
        with old.begin() as connection,pytest.raises(DBAPIError):
            connection.execute(text("UPDATE queue_items SET status='posting' WHERE id=:id"),{'id':qid})
    finally:old.dispose()
    with SessionLocal() as s:assert s.get(QueueItem,qid).status==state


def test_current_connections_can_record_posting_and_owner_receipt_recovery():
    with SessionLocal() as s:
        item=QueueItem(title='Synthetic current capability',status='review');s.add(item);s.commit();qid=item.id
        item.status='posting';item.publish_requests=[{'state':'accepted','request_id':'synthetic-owned-recovery'}];s.commit()
        assert s.get(QueueItem,qid).status=='posting'


def test_unmarked_connection_can_still_read_retained_queue_and_edit_private_notes():
    with SessionLocal() as s:
        item=QueueItem(title='Synthetic read-only rollback record',status='ready');s.add(item);s.commit();qid=item.id
    old=create_engine(engine.url)
    try:
        with old.begin() as connection:
            assert connection.execute(text('SELECT title FROM queue_items WHERE id=:id'),{'id':qid}).scalar()=='Synthetic read-only rollback record'
            connection.execute(text("UPDATE queue_items SET notes='Preserved private rollback note' WHERE id=:id"),{'id':qid})
    finally:old.dispose()


def test_pinned_baseline_publisher_is_stopped_at_its_database_write(tmp_path):
    import ast,asyncio,subprocess
    from pathlib import Path
    from sqlalchemy.orm import Session
    from backend.app.social import queue_manager as qm
    baseline='95b6bd45c23644cff2ec4549db4312c2f7d1281e'
    original=subprocess.check_output(['git','show',baseline+':backend/app/social/queue_manager.py'],
        cwd=Path(__file__).resolve().parents[2],text=True)
    function=next(node for node in ast.parse(original).body if isinstance(node,ast.AsyncFunctionDef) and node.name=='publish_queue_item')
    # Execute the exact old function against an independently created old-style
    # session. Imports/helpers use the test environment; the provider is fake.
    namespace=dict(qm.__dict__);calls=[]
    async def submit(*args,**kwargs):calls.append('must not submit');return [{'request_id':'synthetic-must-not-submit'}]
    namespace.update(submit_upload=submit,needs_auto_seo=lambda item:False)
    exec(compile(ast.Module(body=[function],type_ignores=[]),baseline+':publish_queue_item','exec'),namespace)
    file=tmp_path/'opaque-legacy-transport-fixture.mp4';file.write_bytes(b'isolated opaque fixture, not quality proof')
    with SessionLocal() as s:
        item=QueueItem(title='Pinned old publisher fixture',pipeline='LongForm',source='LongForm Studio',
            status='ready',accounts=['default:youtube'],video_path=str(file))
        s.add(item);s.commit();qid=item.id
    old=create_engine(engine.url)
    try:
        with Session(old) as session,pytest.raises(DBAPIError):asyncio.run(namespace['publish_queue_item'](qid,session))
    finally:old.dispose()
    assert not calls
    with SessionLocal() as s:assert s.get(QueueItem,qid).status=='ready'
