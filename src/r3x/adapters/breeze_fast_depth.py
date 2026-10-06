"""Give Breeze's depth decoder a KV cache (V2 audio/breeze_fast_depth.py, HEAD fb96f08).

mlx-audio 0.5.1 decodes the 16 codec codebooks by re-running the depth decoder over
the whole growing prefix at every step (2 + 3 + ... + 16 = 135 positions per frame
where 16 would do), and that decoder is about 91% of the generation loop. This
incremental version primes positions 0 and 1 (backbone state + codebook 0) and then
feeds one new token per step with a cache. Positions, RoPE offsets, the per-codebook
embedding offset and per-step output head follow the original layout. V2's bench
found 4-bit bit-identical; 8-bit can diverge through bf16 rounding, so this is a
speed port, not a bit-exact transform. Imports mlx only when enabled.
"""


def enable_fast_depth() -> bool:
    """Patch the installed Breeze model class once. Returns True when it changed."""
    import mlx.core as mx
    from mlx_audio.lm.models.cache import KVCache
    from mlx_audio.tts.models.breeze_tts.breeze_tts import Model
    if getattr(Model, '_r3x_fast_depth', False):
        return False

    def _depth_tokens_cached(self, first_codebook, conditional_hidden, *, unconditional_hidden,
                             cfg_scale, temperature, top_p, top_k):
        depth = self.depth_decoder.model
        heads = self.depth_decoder.codebooks_head.weight
        vocab = depth.vocab_size
        layers = depth.layers

        def project(hidden):
            if depth.backbone_hidden_state_projector is not None:
                return depth.backbone_hidden_state_projector(hidden)
            return hidden

        def forward(embeds, cache):
            hidden = depth.inputs_embeds_projector(embeds)
            mask = 'causal' if hidden.shape[1] > 1 else None   # Only the two-position priming step is causal.
            for layer, layer_cache in zip(layers, cache):
                hidden = layer(hidden, mask, layer_cache)
            return depth.norm(hidden)[:, -1, :]

        batch = conditional_hidden.shape[0]
        first = mx.full((batch, 1), first_codebook, dtype=mx.int32)
        prime = mx.concatenate([project(conditional_hidden)[:, None, :], depth.embed_tokens(first)], axis=1)
        cond_cache = [KVCache() for _ in layers]
        cond_hidden = forward(prime, cond_cache)
        uncond_cache = uncond_out = None
        if unconditional_hidden is not None:
            uncond_prime = mx.concatenate([project(unconditional_hidden)[:, None, :], depth.embed_tokens(first)], axis=1)
            uncond_cache = [KVCache() for _ in layers]
            uncond_out = forward(uncond_prime, uncond_cache)
        codes = [first_codebook]
        last = self.num_codebooks - 2
        for step in range(self.num_codebooks - 1):
            logits = cond_hidden @ heads[step]
            if uncond_out is not None:
                unconditional = uncond_out @ heads[step]
                logits = unconditional + cfg_scale * (logits - unconditional)
            logits = self._mask_reserved_codec_logits(logits)
            token = self._sample(logits, temperature=temperature, top_p=top_p, top_k=top_k)
            codes.append(token)
            if step == last:
                break
            # Codebook step+1 sits at position step+1 via the embedding offset (step+1)*vocab.
            nxt = depth.embed_tokens(mx.full((batch, 1), token + (step + 1) * vocab, dtype=mx.int32))
            cond_hidden = forward(nxt, cond_cache)
            if uncond_cache is not None:
                uncond_out = forward(nxt, uncond_cache)
        return codes

    Model._depth_tokens = _depth_tokens_cached
    Model._r3x_fast_depth = True
    return True
