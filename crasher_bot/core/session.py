"""Session recovery – find previous session in page data and backfill."""

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from crasher_bot.core import Database

logger = logging.getLogger(__name__)


def find_session_in_page(
    db: Database,
    page_mults: List[float],
    min_consecutive: int = 5,
) -> Optional[Tuple[int, int, List[float]]]:
    """
    Match the most recent DB session against multipliers read from the page.

    Returns (session_id, match_end_position, missing_rounds) or None.
    """
    last = db.get_last_session()
    if not last:
        return None

    session_id, _, round_count = last
    if round_count == 0:
        return (session_id, 0, page_mults)

    max_pat = min(round_count, 20)
    for plen in range(max_pat, min_consecutive - 1, -1):
        db_pat = db.get_session_multipliers(session_id, plen)
        if not db_pat:
            continue
        for i in range(len(page_mults) - plen + 1):
            chunk = page_mults[i : i + plen]
            if all(abs(a - b) < 0.01 for a, b in zip(db_pat, chunk)):
                end = i + plen
                missing = page_mults[end:]
                logger.info(
                    "Session #%d matched (pattern=%d, missing=%d)",
                    session_id,
                    plen,
                    len(missing),
                )
                return (session_id, end, missing)

    logger.info("Could not match session #%d in page data", session_id)
    return None


def recover_or_create(
    db: Database,
    page_mults: List[float],
    start_balance: Optional[float] = None,
    import_on_new: bool = True,
) -> int:
    """Recover an existing session or create a new one. Returns session_id."""
    if not page_mults:
        sid = db.create_session(start_balance)
        logger.info("Created new session #%d (no page data)", sid)
        return sid

    match = find_session_in_page(db, page_mults)

    if match:
        sid, _, missing = match
        db.current_session_id = sid
        if missing:
            last_info = db.get_last_session()
            last_ts = (
                datetime.fromisoformat(last_info[1])
                if last_info and last_info[1]
                else datetime.now() - timedelta(seconds=60 * len(missing))
            )
            db.add_missing_rounds(sid, missing, last_ts, datetime.now())
            logger.info("Backfilled %d rounds into session #%d", len(missing), sid)
        return sid

    sid = db.create_session(start_balance)
    if import_on_new and page_mults:
        now = datetime.now()
        start = now - timedelta(seconds=30 * len(page_mults))
        db.add_missing_rounds(sid, page_mults, start, now)
        logger.info("Imported %d rounds into new session #%d", len(page_mults), sid)
    return sid
