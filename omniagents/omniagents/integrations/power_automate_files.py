from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

logger = logging.getLogger(__name__)
MAX_CHUNK_TEXT = 1048576
MAX_FILE_BYTES = 1073741824


def download_attachment(connection, container, attachment_id, source, directory):
    identity = UUID(str(attachment_id))
    row = connection.execute(
        "SELECT blob_name, sha256, byte_size FROM omni_bridge.attachments WHERE id=%s AND source=%s AND state='ready'",
        (identity, source),
    ).fetchone()
    if row is None:
        raise ValueError("attachment_not_available")
    blob_name, expected_hash, expected_size = row
    if not 0 <= expected_size <= MAX_FILE_BYTES:
        raise ValueError("invalid_file_size")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / str(identity)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
            temporary = Path(output.name)
            checksum = hashlib.sha256()
            size = 0
            for chunk in container.get_blob_client(blob_name).download_blob().chunks():
                size += len(chunk)
                if size > expected_size:
                    raise ValueError("invalid_file_size")
                checksum.update(chunk)
                output.write(chunk)
            if size != expected_size or checksum.hexdigest() != expected_hash:
                raise ValueError("checksum_mismatch")
        os.link(temporary, target)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def assemble_attachment(manifest, chunks, destination):
    expected_size = manifest["byte_size"]
    expected_count = manifest["chunk_count"]
    if not 0 <= expected_size <= MAX_FILE_BYTES or not 1 <= expected_count <= 2048:
        raise ValueError("invalid_manifest")
    digest = hashlib.sha256()
    byte_size = 0
    count = 0
    for chunk_index, content in chunks:
        if chunk_index != count or count >= expected_count:
            raise ValueError("invalid_chunk_sequence")
        if not isinstance(content, str) or len(content) > MAX_CHUNK_TEXT:
            raise ValueError("invalid_chunk_size")
        try:
            decoded = base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("invalid_chunk_encoding") from None
        byte_size += len(decoded)
        if byte_size > expected_size:
            raise ValueError("invalid_file_size")
        destination.write(decoded)
        digest.update(decoded)
        count += 1
    if count != expected_count or byte_size != expected_size:
        raise ValueError("incomplete_transfer")
    checksum = digest.hexdigest()
    if manifest.get("expected_sha256") and manifest["expected_sha256"] != checksum:
        raise ValueError("checksum_mismatch")
    destination.seek(0)
    return checksum


def process_one(connection, container):
    from azure.core.exceptions import ResourceExistsError
    from azure.storage.blob import ContentSettings
    from psycopg.rows import dict_row

    with connection.transaction():
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM omni_bridge.attachments WHERE state='complete' ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
            manifest = cursor.fetchone()
        if manifest is None:
            return False
        with tempfile.TemporaryFile() as output:
            try:
                with connection.cursor(name="attachment_chunks") as cursor:
                    cursor.execute(
                        "SELECT chunk_index, content FROM omni_bridge.attachment_chunks WHERE attachment_id=%s ORDER BY chunk_index",
                        (manifest["id"],),
                    )
                    checksum = assemble_attachment(manifest, cursor, output)
            except ValueError as error:
                connection.execute(
                    "UPDATE omni_bridge.attachments SET state='rejected', error_code=%s WHERE id=%s",
                    (str(error), manifest["id"]),
                )
                return True
            blob_name = f"attachments/{manifest['id']}/{checksum}"
            blob = container.get_blob_client(blob_name)
            metadata = {"sha256": checksum, "attachment_id": str(manifest["id"])}
            try:
                blob.upload_blob(
                    output,
                    length=manifest["byte_size"],
                    overwrite=False,
                    metadata=metadata,
                    content_settings=ContentSettings(
                        content_type="application/octet-stream"
                    ),
                    max_concurrency=1,
                )
            except ResourceExistsError:
                properties = blob.get_blob_properties()
                if (
                    properties.size != manifest["byte_size"]
                    or properties.metadata != metadata
                ):
                    raise ValueError("existing_blob_mismatch") from None
            connection.execute(
                "UPDATE omni_bridge.attachments SET state='ready', blob_name=%s, sha256=%s WHERE id=%s",
                (blob_name, checksum, manifest["id"]),
            )
            connection.execute(
                "DELETE FROM omni_bridge.attachment_chunks WHERE attachment_id=%s",
                (manifest["id"],),
            )
    return True


def fetch_attachment(connection_string, attachment_id, source, directory):
    import psycopg
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient
    from psycopg.conninfo import conninfo_to_dict

    if conninfo_to_dict(connection_string).get("sslmode") != "verify-full":
        raise ValueError("bridge requires sslmode=verify-full")
    account_url = os.environ["OMNI_BRIDGE_BLOB_URL"]
    validate_blob_url(account_url)
    with (
        ManagedIdentityCredential(
            client_id=os.environ.get("AZURE_CLIENT_ID")
        ) as credential,
        BlobServiceClient(
            account_url, credential=credential, connection_timeout=10, read_timeout=60
        ) as service,
        psycopg.connect(
            connection_string, connect_timeout=10, options="-c statement_timeout=60000"
        ) as connection,
    ):
        return download_attachment(
            connection,
            service.get_container_client(os.environ["OMNI_BRIDGE_BLOB_CONTAINER"]),
            attachment_id,
            source,
            directory,
        )


def validate_blob_url(account_url):
    parsed = urlparse(account_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".blob.core.windows.net")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port
        or parsed.path not in ("", "/")
    ):
        raise ValueError("invalid Azure Blob endpoint")


def main():
    import psycopg
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient
    from psycopg.conninfo import conninfo_to_dict

    logging.basicConfig(level=logging.INFO)
    connection_string = os.environ["OMNI_POWER_AUTOMATE_DSN"]
    if conninfo_to_dict(connection_string).get("sslmode") != "verify-full":
        raise ValueError("bridge requires sslmode=verify-full")
    account_url = os.environ["OMNI_BRIDGE_BLOB_URL"]
    validate_blob_url(account_url)
    credential = ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID"))
    with (
        credential,
        BlobServiceClient(
            account_url, credential=credential, connection_timeout=10, read_timeout=60
        ) as service,
    ):
        container = service.get_container_client(
            os.environ["OMNI_BRIDGE_BLOB_CONTAINER"]
        )
        while True:
            try:
                with psycopg.connect(
                    connection_string,
                    connect_timeout=10,
                    options="-c statement_timeout=60000 -c idle_in_transaction_session_timeout=600000",
                ) as connection:
                    while process_one(connection, container):
                        pass
            except Exception:
                logger.warning(
                    "Attachment bridge unavailable; pending transfers retained"
                )
            time.sleep(5)


if __name__ == "__main__":
    main()
