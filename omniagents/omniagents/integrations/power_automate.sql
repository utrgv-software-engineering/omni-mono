BEGIN;
CREATE SCHEMA omni_bridge;
REVOKE ALL ON SCHEMA omni_bridge FROM PUBLIC;
CREATE TABLE omni_bridge.inbound_events (
    id text PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 512),
    source text NOT NULL CHECK (length(source) BETWEEN 1 AND 128),
    payload text NOT NULL CHECK (octet_length(payload) <= 1048576),
    created_at timestamptz NOT NULL DEFAULT now(),
    available_at timestamptz NOT NULL DEFAULT now(),
    accepted_at timestamptz,
    rejected_at timestamptz,
    error_code text
);
CREATE INDEX inbound_pending ON omni_bridge.inbound_events (created_at, id)
    WHERE accepted_at IS NULL AND rejected_at IS NULL;
CREATE TABLE omni_bridge.outbound_actions (
    id text PRIMARY KEY,
    source text NOT NULL,
    operation text NOT NULL CHECK (operation IN ('teams.reply', 'sharepoint.get_file', 'sharepoint.update_item', 'outlook.send_email')),
    payload text NOT NULL CHECK (octet_length(payload) <= 131072),
    agent text,
    session_id text,
    result_attachment_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'sending', 'succeeded', 'failed', 'uncertain')),
    state_changed_at timestamptz NOT NULL DEFAULT now(),
    result text CHECK (octet_length(result) <= 131072)
);
CREATE INDEX outbound_pending ON omni_bridge.outbound_actions (created_at, id)
    WHERE state = 'pending';
CREATE INDEX outbound_sending ON omni_bridge.outbound_actions (state_changed_at, id)
    WHERE state = 'sending';
CREATE TABLE omni_bridge.attachments (
    id uuid PRIMARY KEY,
    source text NOT NULL,
    filename text NOT NULL CHECK (length(filename) BETWEEN 1 AND 255),
    content_type text NOT NULL,
    byte_size bigint NOT NULL CHECK (byte_size BETWEEN 0 AND 1073741824),
    chunk_count integer NOT NULL CHECK (chunk_count BETWEEN 1 AND 2048),
    expected_sha256 text CHECK (expected_sha256 ~ '^[0-9a-f]{64}$'),
    state text NOT NULL DEFAULT 'uploading' CHECK (state IN ('uploading', 'complete', 'ready', 'rejected')),
    blob_name text,
    sha256 text,
    error_code text,
    state_changed_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX attachments_unfinished ON omni_bridge.attachments (state_changed_at, id)
    WHERE state IN ('uploading', 'complete', 'rejected');
CREATE TABLE omni_bridge.attachment_chunks (
    id text PRIMARY KEY,
    attachment_id uuid NOT NULL REFERENCES omni_bridge.attachments(id),
    chunk_index integer NOT NULL CHECK (chunk_index BETWEEN 0 AND 2047),
    content text NOT NULL CHECK (octet_length(content) <= 1048576),
    UNIQUE (attachment_id, chunk_index)
);
COMMIT;
