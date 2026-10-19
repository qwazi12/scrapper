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
