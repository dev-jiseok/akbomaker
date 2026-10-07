"""Optional constrained greedy decoding for the plain ADT_STR token format.

This is an application-side adapter, not a modification of the upstream model.
It constrains syntax and exact duplicate events, not musical plausibility. A
valid sequence can still contain wrong or missing notes, so compare it against
labelled audio before enabling it by default.

Usage, after checking the upstream tokenizer uses the custom 26-class mapping::

    model.model._akbo_add_velocity = model.tokenizer.add_velocity
    model.model.sample = types.MethodType(constrained_sample, model.model)

The sampler returns the same tensor shape as the upstream method (batch size 1
only). Its most recent call's diagnostics are in ``_akbo_decoding_stats``. Copy
that dictionary after each chunk if a caller needs a whole-file audit trail.
Importing this module does not import torch; only the sampler requires it.
"""

from dataclasses import dataclass, field
import math

from .drum_mapping import ADT_TO_GM


BOS, EOS, SILENCE = 2, 3, 0
PITCH_TOKENS = tuple(300 + pitch for pitch in sorted(ADT_TO_GM))
VELOCITY_TOKENS = tuple(range(401, 528))


@dataclass
class ADTTokenGrammar:
    """Incremental finite-state grammar with a complete-sequence token budget.

    ``max_length`` includes BOS and EOS. Every new event reserves three tokens
    plus EOS. Equal onsets are allowed for different pitches, in any pitch
    order; the same onset/pitch pair cannot occur twice. All upstream custom
    percussion classes are retained, even those absent from the score editor.
    """

    input_seconds: float = 2.56
    max_length: int = 512
    add_velocity: bool = True
    tokens: list[int] = field(default_factory=lambda: [BOS], init=False)
    phase: str = field(default="onset", init=False)
    previous_onset: int = field(default=4, init=False)
    pending_onset: int | None = field(default=None, init=False)
    pending_pitch: int | None = field(default=None, init=False)
    seen: set[tuple[int, int]] = field(default_factory=set, init=False)
    _onset_stop: int = field(init=False, repr=False)

    def __post_init__(self):
        if not self.add_velocity:
            raise ValueError("Constrained ADT decoding requires add_velocity=True")
        if (isinstance(self.max_length, bool) or not isinstance(self.max_length, int)
                or self.max_length < 2):
            raise ValueError("max_length must be an integer of at least 2 (BOS and EOS)")
        if (not math.isfinite(self.input_seconds) or self.input_seconds <= 0
                or self.input_seconds > 2.96):
            raise ValueError("input_seconds must be positive and at most 2.96")
        self._onset_stop = 4 + math.ceil(self.input_seconds * 100)

    @property
    def finished(self):
        return self.phase == "done"

    @property
    def remaining(self):
        return self.max_length - len(self.tokens)

    def allowed_tokens(self):
        if self.finished:
            return ()
        if self.phase == "silence":
            return (EOS,)
        if self.phase == "pitch":
            return tuple(token for token in PITCH_TOKENS
                         if (self.pending_onset, token) not in self.seen)
        if self.phase == "velocity":
            return VELOCITY_TOKENS
        allowed = [EOS]
        if len(self.tokens) == 1 and self.remaining >= 2:
            allowed.append(SILENCE)
        if self.remaining >= 4:
            allowed.extend(onset for onset in range(self.previous_onset, self._onset_stop)
                           if any((onset, pitch) not in self.seen for pitch in PITCH_TOKENS))
        return tuple(allowed)

    def consume(self, token):
        if isinstance(token, bool) or not isinstance(token, int) or token not in self.allowed_tokens():
            raise ValueError(f"Token {token!r} is invalid in ADT phase {self.phase}")
        self.tokens.append(token)
        if token == EOS:
            self.phase = "done"
        elif self.phase == "onset":
            if token == SILENCE:
                self.phase = "silence"
            else:
                self.pending_onset = token
                self.phase = "pitch"
        elif self.phase == "pitch":
            self.pending_pitch = token
            self.phase = "velocity"
        elif self.phase == "velocity":
            self.seen.add((self.pending_onset, self.pending_pitch))
            self.previous_onset = self.pending_onset
            self.pending_onset = self.pending_pitch = None
            self.phase = "onset"


def constrained_sample(self, src, src_mask, tgt_mask, max_length=1000, start_token=2, end_token=3):
    """Drop-in, batch-one greedy sampler using ADTTokenGrammar.

    The two incoming masks are unused, matching upstream greedy sampling. The
    adapter builds a boolean causal mask for each full-prefix decoder pass.
    Masked argmax does not repair already-generated notes or suppress classes.
    A non-finite model output fails explicitly instead of creating notes from
    arbitrary fallback probabilities.
    """
    if not self.config.plain:
        raise ValueError("Constrained ADT decoding requires plain=True")
    if start_token != BOS or end_token != EOS:
        raise ValueError("Constrained ADT decoding requires BOS=2 and EOS=3")
    if src.ndim != 2 or src.shape[0] != 1:
        raise ValueError("Constrained ADT decoding supports batch size 1 only")
    grammar = ADTTokenGrammar(input_seconds=self.config.input_sec, max_length=max_length,
                              add_velocity=getattr(self, "_akbo_add_velocity", True))
    import torch

    self.eval()
    stats = {"decoder": "constrained-greedy-v1", "forced_tokens": 0,
             "duplicate_pitch_avoided": 0, "backwards_onset_avoided": 0,
             "budget_limited": False, "events": 0, "tokens": 1}
    self._akbo_decoding_stats = stats
    with torch.inference_mode():
        memory = self.encoder(self.project_to_mel(self.compute_spectrogram(src)))
        generated = torch.full((1, 1), BOS, dtype=torch.long, device=src.device)
        while not grammar.finished:
            seq_len = generated.shape[1]
            causal = torch.triu(torch.ones((seq_len, seq_len), dtype=torch.bool,
                                          device=src.device), diagonal=1)
            logits = self.decoder(generated, memory, causal, tgt_padding_mask=None)[0, -1, :]
            if not torch.isfinite(logits).all().item():
                raise ValueError("ADT decoder produced non-finite logits")
            allowed = grammar.allowed_tokens()
            if max(allowed) >= logits.shape[-1]:
                raise ValueError("ADT decoder vocabulary does not cover the configured token grammar")
            unconstrained = int(torch.argmax(logits).item())
            candidates = torch.tensor(allowed, dtype=torch.long, device=logits.device)
            selected = int(candidates[torch.argmax(logits[candidates])].item())
            stats["forced_tokens"] += selected != unconstrained
            if grammar.phase == "pitch" and (grammar.pending_onset, unconstrained) in grammar.seen:
                stats["duplicate_pitch_avoided"] += 1
            if (grammar.phase == "onset" and 4 <= unconstrained < grammar.previous_onset):
                stats["backwards_onset_avoided"] += 1
            if (grammar.phase == "onset" and grammar.remaining < 4
                    and selected == EOS and unconstrained != EOS):
                stats["budget_limited"] = True
            grammar.consume(selected)
            generated = torch.cat((generated, generated.new_tensor([[selected]])), dim=1)
        stats["events"] = len(grammar.seen)
        stats["tokens"] = len(grammar.tokens)
    return generated
