BEGIN;
CREATE ROLE omni_bridge_worker NOLOGIN;
CREATE ROLE omni_bridge_flow NOLOGIN;
GRANT USAGE ON SCHEMA omni_bridge TO omni_bridge_worker, omni_bridge_flow;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA omni_bridge TO omni_bridge_worker;
GRANT SELECT ON ALL TABLES IN SCHEMA omni_bridge TO omni_bridge_flow;
GRANT INSERT (id, source, payload, created_at, available_at) ON omni_bridge.inbound_events TO omni_bridge_flow;
GRANT UPDATE (source, operation, payload, created_at, state, state_changed_at, result, result_attachment_id) ON omni_bridge.outbound_actions TO omni_bridge_flow;
GRANT INSERT (id, source, filename, content_type, byte_size, chunk_count, expected_sha256) ON omni_bridge.attachments TO omni_bridge_flow;
GRANT UPDATE (state) ON omni_bridge.attachments TO omni_bridge_flow;
GRANT INSERT ON omni_bridge.attachment_chunks TO omni_bridge_flow;
ALTER TABLE omni_bridge.inbound_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE omni_bridge.outbound_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE omni_bridge.attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE omni_bridge.attachment_chunks ENABLE ROW LEVEL SECURITY;
CREATE POLICY worker ON omni_bridge.inbound_events TO omni_bridge_worker USING (true) WITH CHECK (true);
CREATE POLICY worker ON omni_bridge.outbound_actions TO omni_bridge_worker USING (true) WITH CHECK (true);
CREATE POLICY worker ON omni_bridge.attachments TO omni_bridge_worker USING (true) WITH CHECK (true);
CREATE POLICY worker ON omni_bridge.attachment_chunks TO omni_bridge_worker USING (true) WITH CHECK (true);
CREATE POLICY flow ON omni_bridge.inbound_events TO omni_bridge_flow USING (source = current_user) WITH CHECK (source = current_user);
CREATE POLICY flow ON omni_bridge.outbound_actions TO omni_bridge_flow USING (source = current_user) WITH CHECK (source = current_user);
CREATE POLICY flow ON omni_bridge.attachments TO omni_bridge_flow USING (source = current_user) WITH CHECK (source = current_user);
CREATE POLICY flow ON omni_bridge.attachment_chunks TO omni_bridge_flow
    USING (EXISTS (SELECT 1 FROM omni_bridge.attachments WHERE id = attachment_id AND source = current_user))
    WITH CHECK (EXISTS (SELECT 1 FROM omni_bridge.attachments WHERE id = attachment_id AND source = current_user AND state = 'uploading'));
CREATE FUNCTION omni_bridge.guard_transfer() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'attachment_chunks' THEN
        PERFORM id FROM omni_bridge.attachments WHERE id = NEW.attachment_id AND state = 'uploading' FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'transfer is not accepting chunks';
        END IF;
    ELSIF NOT pg_has_role(current_user, 'omni_bridge_worker', 'member') THEN
        IF OLD.state <> 'uploading' OR NEW.state <> 'complete' THEN
            RAISE EXCEPTION 'invalid transfer transition';
        END IF;
    END IF;
    IF TG_TABLE_NAME = 'attachments' THEN
        IF NEW.state IS DISTINCT FROM OLD.state THEN
            NEW.state_changed_at = clock_timestamp();
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_chunk BEFORE INSERT ON omni_bridge.attachment_chunks FOR EACH ROW EXECUTE FUNCTION omni_bridge.guard_transfer();
CREATE TRIGGER guard_manifest BEFORE UPDATE ON omni_bridge.attachments FOR EACH ROW EXECUTE FUNCTION omni_bridge.guard_transfer();
CREATE FUNCTION omni_bridge.guard_action() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT pg_has_role(current_user, 'omni_bridge_worker', 'member') THEN
        NEW.id = OLD.id;
        NEW.source = OLD.source;
        NEW.operation = OLD.operation;
        NEW.payload = OLD.payload;
        NEW.agent = OLD.agent;
        NEW.session_id = OLD.session_id;
        NEW.created_at = OLD.created_at;
        NEW.state_changed_at = OLD.state_changed_at;
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state THEN
        IF NOT (
            (OLD.state = 'pending' AND NEW.state = 'sending') OR
            (OLD.state = 'sending' AND NEW.state IN ('succeeded', 'failed', 'uncertain')) OR
            (OLD.state = 'uncertain' AND NEW.state IN ('succeeded', 'failed')
                AND pg_has_role(current_user, 'omni_bridge_worker', 'member'))
        ) THEN
            RAISE EXCEPTION 'invalid action transition';
        END IF;
        NEW.state_changed_at = clock_timestamp();
    ELSIF NOT pg_has_role(current_user, 'omni_bridge_worker', 'member') THEN
        IF OLD.state = 'sending' OR NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'action already claimed or terminal';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_action BEFORE UPDATE ON omni_bridge.outbound_actions FOR EACH ROW EXECUTE FUNCTION omni_bridge.guard_action();
REVOKE ALL ON FUNCTION omni_bridge.guard_action() FROM PUBLIC;
REVOKE ALL ON FUNCTION omni_bridge.guard_transfer() FROM PUBLIC;
COMMIT;
