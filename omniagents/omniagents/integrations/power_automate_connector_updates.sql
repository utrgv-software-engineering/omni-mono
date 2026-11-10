BEGIN;
CREATE OR REPLACE FUNCTION omni_bridge.guard_action() RETURNS trigger LANGUAGE plpgsql AS $$
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
GRANT UPDATE (source, operation, payload, created_at, state_changed_at) ON omni_bridge.outbound_actions TO omni_bridge_flow;
COMMIT;
