"""Selective ADT retry, not a musical correction or a confidence score.

Healthy greedy sequences are returned byte-for-byte. Only malformed/incomplete
chunks are retried, once, with the existing constrained decoder. Original tokens
and the retry stay in the audit so a syntactically valid result is never confused
with an independently verified transcription.
"""
from .adt_decoding import constrained_sample
from .adt_tokens import decode_tokens


METHOD = "guarded-greedy-v2"
# This is an intervention limit, NOT a validated correctness threshold. Even a
# single forced token regressed a labelled recording; keep the mode opt-in.
MAX_FORCED_CORRECTIONS = 1


def sequence_review(tokens, *, input_seconds, add_velocity, max_length):
    _, review = decode_tokens(tokens, input_seconds=input_seconds,
                              add_velocity=add_velocity, max_length=max_length)
    reasons = [key for key in ("invalid_tokens", "incomplete_events", "nonmonotonic_events", "truncated")
               if review[key]]
    if 3 not in review["tokens"]:
        reasons.append("missing_eos")
    if not review["tokens"] or review["tokens"][0] != 2:
        reasons.append("missing_bos")
    if len(review["tokens"]) > max_length:
        reasons.append("over_budget")
    return {**review, "reasons": reasons}


def retrying_sample(self, src, src_mask, tgt_mask, max_length=1000, start_token=2, end_token=3):
    """Install after saving the upstream bound sample as _akbo_greedy_sample."""
    if not self.config.plain or not getattr(self, "_akbo_add_velocity", True):
        raise ValueError("Guarded ADT requires plain velocity triples")
    if start_token != 2 or end_token != 3 or src.ndim != 2 or src.shape[0] != 1:
        raise ValueError("Guarded ADT requires standard tokens and batch size 1")
    # Clear per-chunk state; an earlier retry must not mark a healthy chunk.
    self._akbo_decoding_stats = None
    self._akbo_recovery_stats = None
    options = dict(src=src, src_mask=src_mask, tgt_mask=tgt_mask,
                   max_length=max_length, start_token=start_token, end_token=end_token)
    original = self._akbo_greedy_sample(**options)
    if original.ndim != 2 or original.shape[0] != 1:
        raise ValueError("Invalid ADT sampler output shape")
    review_options = dict(input_seconds=self.config.input_sec,
                          add_velocity=True, max_length=max_length)
    original_review = sequence_review(original[0].tolist(), **review_options)
    audit = {"method": METHOD, "attempted": False, "selected": "greedy",
             "unresolved": False, "syntax_is_accuracy": False}
    self._akbo_recovery_stats = audit
    if not original_review["reasons"]:
        return original
    audit.update(attempted=True, original=original_review)
    retry = constrained_sample(self, **options)
    retry_review = sequence_review(retry[0].tolist(), **review_options)
    sampling = dict(self._akbo_decoding_stats or {})
    audit.update(retry=retry_review, sampling=sampling)
    rejected = list(retry_review["reasons"])
    if sampling.get("budget_limited"):
        rejected.append("retry_budget_limited")
    forced = sampling.get("forced_tokens")
    if type(forced) is not int or forced < 0:
        rejected.append("invalid_intervention_audit")
    elif forced > MAX_FORCED_CORRECTIONS:
        rejected.append("excessive_intervention")
    audit["maximum_forced_corrections"] = MAX_FORCED_CORRECTIONS
    audit["rejected_reasons"] = rejected
    if rejected:
        # Keep the original evidence, do not fabricate a clean ending or suppress
        # a potentially real note merely to produce a valid-looking sequence.
        audit["unresolved"] = True
        self._akbo_decoding_stats = None  # Retry stats belong only to audit.sampling.
        return original
    audit["selected"] = "constrained"
    return retry
