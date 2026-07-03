"""Auto-reply to direct callers when the core auto-reply switch is enabled."""

import logging


logger = logging.getLogger(__name__)

ORDER = 50


def on_decode_batch(ctx, decodes):
    candidates = []
    skipped_not_calling_me = 0
    skipped_73 = 0
    skipped_no_call = 0
    for decode in decodes:
        message = str(decode.get("message") or "")
        if not ctx.is_calling_own(message):
            skipped_not_calling_me += 1
            continue
        if _is_73_message(message):
            skipped_73 += 1
            continue
        call = ctx.extract_callsign(message)
        if not call or call == "UNKNOWN":
            skipped_no_call += 1
            continue
        candidates.append((int(decode.get("snr") or -999), ctx.normalize_call(call), decode))

    if not candidates:
        logger.info(
            "direct_call auto reply no candidate total=%d skipped_not_calling_me=%d skipped_73=%d skipped_no_call=%d",
            len(decodes),
            skipped_not_calling_me,
            skipped_73,
            skipped_no_call,
        )
        return None

    snr, call, decode = max(candidates, key=lambda item: item[0])
    logger.info(
        "direct_call auto reply candidate call=%s snr=%s decode_index=%s candidates=%s skipped_not_calling_me=%d skipped_73=%d skipped_no_call=%d",
        call,
        snr,
        decode.get("index"),
        [(candidate_call, candidate_snr, candidate_decode.get("index")) for candidate_snr, candidate_call, candidate_decode in sorted(candidates, reverse=True)],
        skipped_not_calling_me,
        skipped_73,
        skipped_no_call,
    )
    return decode


def _is_73_message(message):
    return any(word == "73" for word in message.upper().split())
