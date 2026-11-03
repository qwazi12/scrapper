"""Versioned database write fence, not provider authorization or source proof.

Older runtimes may read the database but cannot write posting status through an
unmarked connection. This cannot recall an already in-flight provider request
or stop old code that submits without any database write; stop old publishers
before upgrading/rolling back. Future protocols add fences; never drop v1.
"""
from sqlalchemy import event,text

PROTOCOL='original-studio-source-v1'


def mark_connections(engine):
    @event.listens_for(engine,'connect')
    def connected(connection,record):
        if engine.dialect.name=='sqlite':
            connection.create_function('scrapper_publisher_protocol_v1',0,lambda:1)
        elif engine.dialect.name=='postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SET scrapper.publisher_protocol_v1 = 'original-studio-source-v1'")
            connection.commit()  # New connection; keep its marker across pool rollbacks.


def install_fence(engine):
    with engine.begin() as connection:
        if engine.dialect.name=='sqlite':
            connection.execute(text("""CREATE TRIGGER IF NOT EXISTS scrapper_publisher_protocol_v1
                BEFORE UPDATE OF status ON queue_items WHEN NEW.status='posting'
                BEGIN SELECT CASE WHEN coalesce(scrapper_publisher_protocol_v1(),0)<>1
                THEN RAISE(ABORT,'Publisher protocol v1 required; stop incompatible publishers') END; END"""))
            # Built-in SQL only: resolving a missing UDF in a conditional SQLite
            # trigger also breaks unrelated rows on old connections. A one-use
            # grant is inserted and consumed inside the new atomic intent claim.
            connection.execute(text("""CREATE TRIGGER IF NOT EXISTS scrapper_drama_protocol_v1
                BEFORE UPDATE OF status ON queue_items
                WHEN NEW.status='posting' AND (NEW.pipeline='DramaRama' OR EXISTS
                    (SELECT 1 FROM drama_episodes e WHERE e.queue_id=NEW.id AND e.queue_created_at=NEW.created_at))
                BEGIN SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM drama_dispatch_permits p
                    WHERE p.queue_id=NEW.id AND p.queue_created_at=NEW.created_at
                    AND p.attempt_id=json_extract(NEW.publish_requests,'$[0].client_request_id')
                    AND julianday(p.expires_at)>julianday('now'))
                THEN RAISE(ABORT,'Drama atomic dispatch permit required; preserve the guard on rollback') END; END"""))
            connection.execute(text("""CREATE TRIGGER IF NOT EXISTS scrapper_drama_permit_consume_v1
                AFTER UPDATE OF status ON queue_items
                WHEN NEW.status='posting' AND (NEW.pipeline='DramaRama' OR EXISTS
                    (SELECT 1 FROM drama_episodes e WHERE e.queue_id=NEW.id AND e.queue_created_at=NEW.created_at))
                BEGIN DELETE FROM drama_dispatch_permits WHERE queue_id=NEW.id; END"""))
            # Guard follows the recorded immutable identity, even if an older
            # runtime changes the pipeline or replans its reserved future slot.
            # Millisecond matching can conservatively hold a same-ms ID reuse;
            # it cannot permit an incompatible dispatch of the original row.
            ick_identity="""EXISTS (SELECT 1 FROM metadata_mutation_receipts r
                WHERE r.queue_id=NEW.id AND json_extract(r.data,'$.scope')='ick-future-ready-v1'
                AND strftime('%Y-%m-%d %H:%M:%f',json_extract(r.data,'$.before.protected_fields.created_at'))
                    =strftime('%Y-%m-%d %H:%M:%f',NEW.created_at))"""
            connection.execute(text(f"""CREATE TRIGGER IF NOT EXISTS scrapper_ick_protocol_v1
                BEFORE UPDATE OF status ON queue_items WHEN NEW.status='posting' AND {ick_identity}
                BEGIN SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM drama_dispatch_permits p
                    WHERE p.queue_id=NEW.id AND p.queue_created_at=NEW.created_at
                    AND p.attempt_id=json_extract(NEW.publish_requests,'$[0].client_request_id')
                    AND julianday(p.expires_at)>julianday('now'))
                THEN RAISE(ABORT,'ICK atomic dispatch permit required; preserve future-slot guard on rollback') END; END"""))
            connection.execute(text(f"""CREATE TRIGGER IF NOT EXISTS scrapper_ick_permit_consume_v1
                AFTER UPDATE OF status ON queue_items WHEN NEW.status='posting' AND {ick_identity}
                BEGIN DELETE FROM drama_dispatch_permits WHERE queue_id=NEW.id; END"""))
        elif engine.dialect.name=='postgresql':
            connection.execute(text("""CREATE OR REPLACE FUNCTION scrapper_publisher_protocol_v1()
                RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                IF current_setting('scrapper.publisher_protocol_v1',true) IS DISTINCT FROM 'original-studio-source-v1'
                THEN RAISE EXCEPTION 'Publisher protocol v1 required; stop incompatible publishers' USING ERRCODE='23514'; END IF;
                RETURN NEW; END; $$"""))
            exists=connection.execute(text("SELECT 1 FROM pg_trigger WHERE tgname='scrapper_publisher_protocol_v1' AND tgrelid='queue_items'::regclass")).first()
            if not exists:
                connection.execute(text("""CREATE TRIGGER scrapper_publisher_protocol_v1
                    BEFORE UPDATE OF status ON queue_items FOR EACH ROW WHEN (NEW.status='posting')
                    EXECUTE FUNCTION scrapper_publisher_protocol_v1()"""))
            connection.execute(text("""CREATE OR REPLACE FUNCTION scrapper_drama_protocol_v1()
                RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                IF (NEW.pipeline='DramaRama' OR EXISTS (SELECT 1 FROM drama_episodes e
                    WHERE e.queue_id=NEW.id AND e.queue_created_at=NEW.created_at)) AND
                    NOT EXISTS (SELECT 1 FROM drama_dispatch_permits p WHERE p.queue_id=NEW.id
                        AND p.queue_created_at=NEW.created_at AND p.expires_at>clock_timestamp()
                        AND p.attempt_id=NEW.publish_requests->0->>'client_request_id')
                THEN RAISE EXCEPTION 'Drama atomic dispatch permit required; preserve the guard on rollback' USING ERRCODE='23514'; END IF;
                DELETE FROM drama_dispatch_permits WHERE queue_id=NEW.id AND
                    (NEW.pipeline='DramaRama' OR EXISTS (SELECT 1 FROM drama_episodes e
                        WHERE e.queue_id=NEW.id AND e.queue_created_at=NEW.created_at));
                RETURN NEW; END; $$"""))
            exists = connection.execute(text("SELECT 1 FROM pg_trigger WHERE tgname='scrapper_drama_protocol_v1' AND tgrelid='queue_items'::regclass")).first()
            if not exists:
                connection.execute(text("""CREATE TRIGGER scrapper_drama_protocol_v1
                    BEFORE UPDATE OF status ON queue_items FOR EACH ROW
                    WHEN (NEW.status='posting') EXECUTE FUNCTION scrapper_drama_protocol_v1()"""))
            connection.execute(text("""CREATE OR REPLACE FUNCTION scrapper_ick_protocol_v1()
                RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                IF EXISTS (SELECT 1 FROM metadata_mutation_receipts r WHERE r.queue_id=NEW.id
                    AND r.data->>'scope'='ick-future-ready-v1'
                    AND (r.data->'before'->'protected_fields'->>'created_at')::timestamptz=NEW.created_at) THEN
                    IF NOT EXISTS (SELECT 1 FROM drama_dispatch_permits p WHERE p.queue_id=NEW.id
                        AND p.queue_created_at=NEW.created_at AND p.expires_at>clock_timestamp()
                        AND p.attempt_id=NEW.publish_requests->0->>'client_request_id')
                    THEN RAISE EXCEPTION 'ICK atomic dispatch permit required; preserve future-slot guard on rollback' USING ERRCODE='23514'; END IF;
                    DELETE FROM drama_dispatch_permits WHERE queue_id=NEW.id;
                END IF; RETURN NEW; END; $$"""))
            exists=connection.execute(text("SELECT 1 FROM pg_trigger WHERE tgname='scrapper_ick_protocol_v1' AND tgrelid='queue_items'::regclass")).first()
            if not exists:
                connection.execute(text("""CREATE TRIGGER scrapper_ick_protocol_v1 BEFORE UPDATE OF status ON queue_items
                    FOR EACH ROW WHEN (NEW.status='posting') EXECUTE FUNCTION scrapper_ick_protocol_v1()"""))
