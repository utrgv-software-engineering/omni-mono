import logging

logger = logging.getLogger(__name__)


async def maintain(connection, config):
    sources = list(config.sources)
    async with connection.transaction():
        cursor = await connection.execute(
            "WITH stale AS (SELECT id FROM omni_bridge.outbound_actions "
            "WHERE source = ANY(%s) AND state='sending' "
            "AND state_changed_at < now() - make_interval(secs => %s) "
            "ORDER BY state_changed_at, id LIMIT 100 FOR UPDATE SKIP LOCKED) "
            "UPDATE omni_bridge.outbound_actions a SET state='uncertain' "
            "FROM stale WHERE a.id=stale.id RETURNING a.id",
            (sources, config.action_timeout_seconds),
        )
        uncertain = len(await cursor.fetchall())
    async with connection.transaction():
        cursor = await connection.execute(
            "SELECT id FROM omni_bridge.attachments a WHERE source = ANY(%s) AND ("
            "(state IN ('uploading','complete') AND state_changed_at < now() - make_interval(secs => %s)) "
            "OR (state='rejected' AND EXISTS (SELECT 1 FROM omni_bridge.attachment_chunks c WHERE c.attachment_id=a.id))) "
            "ORDER BY state_changed_at, id LIMIT 100 FOR UPDATE SKIP LOCKED",
            (sources, config.transfer_expiry_seconds),
        )
        identities = [row[0] for row in await cursor.fetchall()]
        if identities:
            await connection.execute(
                "UPDATE omni_bridge.attachments SET state='rejected', error_code='transfer_expired' "
                "WHERE id = ANY(%s) AND state IN ('uploading','complete')",
                (identities,),
            )
            await connection.execute(
                "DELETE FROM omni_bridge.attachment_chunks WHERE attachment_id = ANY(%s)",
                (identities,),
            )
    if uncertain or identities:
        logger.warning(
            "Bridge maintenance: %d actions require reconciliation; %d unfinished transfers cleaned",
            uncertain,
            len(identities),
        )
    return {"uncertain_actions": uncertain, "cleaned_transfers": len(identities)}
