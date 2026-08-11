CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS trigger_events ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "event_key TEXT NOT NULL UNIQUE,"
    "payload TEXT NOT NULL,"
    "status TEXT NOT NULL,"
    "available_at REAL NOT NULL,"
    "attempts INTEGER NOT NULL DEFAULT 0,"
    "deferrals INTEGER NOT NULL DEFAULT 0,"
    "last_error TEXT NOT NULL DEFAULT '',"
    "claim_token TEXT NOT NULL DEFAULT '',"
    "lease_until REAL NOT NULL DEFAULT 0,"
    "created_at REAL NOT NULL,"
    "updated_at REAL NOT NULL)"
)
CREATE_READY_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS trigger_events_ready_idx "
    "ON trigger_events(status, available_at, id)"
)
RECOVER_PROCESSING_SQL = (
    "UPDATE trigger_events SET status = 'pending', claim_token = '', "
    "lease_until = 0, updated_at = ? "
    "WHERE status = 'processing' AND lease_until <= ?"
)
INSERT_SQL = (
    "INSERT OR IGNORE INTO trigger_events("
    "event_key,payload,status,available_at,attempts,created_at,updated_at) "
    "VALUES (?,?,'pending',?,0,?,?)"
)
SELECT_READY_SQL = (
    "SELECT id,event_key,payload,attempts,deferrals FROM trigger_events "
    "WHERE status = 'pending' AND available_at <= ? "
    "ORDER BY available_at,id LIMIT 1"
)
MARK_PROCESSING_SQL = (
    "UPDATE trigger_events SET status = 'processing', claim_token = ?, "
    "lease_until = ?, updated_at = ? WHERE id = ? AND status = 'pending'"
)
SELECT_NEXT_SQL = (
    "SELECT MIN(CASE WHEN status = 'pending' THEN available_at ELSE lease_until END) "
    "AS next_available_at FROM trigger_events "
    "WHERE status IN ('pending','processing')"
)
MARK_FAILED_SQL = (
    "UPDATE trigger_events SET payload = ?, status = ?, attempts = ?, available_at = ?, "
    "last_error = ?, claim_token = '', lease_until = 0, updated_at = ? "
    "WHERE id = ? AND status = 'processing' AND claim_token = ?"
)
RELEASE_SQL = (
    "UPDATE trigger_events SET payload = ?, status = 'pending', available_at = ?, "
    "last_error = ?, claim_token = '', lease_until = 0, updated_at = ? "
    "WHERE id = ? AND status = 'processing' AND claim_token = ?"
)
REVIVE_DEAD_LETTER_SQL = (
    "UPDATE trigger_events SET payload = ?, status = 'pending', available_at = ?, "
    "attempts = 0, deferrals = 0, last_error = '', claim_token = '', "
    "lease_until = 0, updated_at = ? "
    "WHERE event_key = ? AND status = 'dead_letter'"
)
REVIVE_RECOVERY_SQL = (
    "UPDATE trigger_events SET payload = ?, status = 'pending', available_at = ?, "
    "attempts = 0, deferrals = 0, last_error = '', claim_token = '', "
    "lease_until = 0, updated_at = ? "
    "WHERE event_key = ? AND status IN ('dead_letter','reconciled')"
)
SELECT_DEAD_LETTERS_SQL = (
    "SELECT id,event_key,payload,attempts,deferrals,last_error FROM trigger_events "
    "WHERE status = 'dead_letter' ORDER BY updated_at DESC LIMIT ?"
)
MARK_DEAD_LETTER_RECONCILED_SQL = (
    "UPDATE trigger_events SET status = 'reconciled', updated_at = ? "
    "WHERE id = ? AND status = 'dead_letter'"
)
DELETE_DEAD_LETTER_SQL = (
    "DELETE FROM trigger_events WHERE id = ? AND status = 'dead_letter'"
)
DELETE_CLAIM_SQL = (
    "DELETE FROM trigger_events "
    "WHERE id = ? AND status = 'processing' AND claim_token = ?"
)
MARK_CORRUPT_SQL = (
    "UPDATE trigger_events SET status = 'dead_letter', attempts = attempts + 1, "
    "last_error = 'corrupt queue payload', claim_token = '', lease_until = 0, "
    "updated_at = ? WHERE id = ?"
)
