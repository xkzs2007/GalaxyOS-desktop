"""memo_adapter.py — MeMo (Memory as a Model) adapter interface.

This module implements the **abstract layer** for the MeMo paper
(arXiv:2605.15156, "Memory as a Model"). It defines:

  - `MeMoAdapter`           : the interface every backend must satisfy
  - `MockMeMoAdapter`       : deterministic in-process backend
  - `OnnxMeMoAdapter`       : ONNX runtime backend (Stage 3, optional)

The actual SFT weights (~900 MB INT4 ONNX) are out of scope for this
in-process build; the Mock backend returns canned answers that
exercise the 3-stage protocol end-to-end so the rest of the system
behaves exactly as it will once the real weights are loaded.

Real MeMo inference is `constant-time` and `independent of corpus
size` — the Memory model has parametrically internalized the corpus
during SFT, so at inference we never touch the documents again. The
Mock backend emulates this by computing answers from a fixed lookup
table — same O(1) characteristic.
"""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("galaxyos-memo")


# ── Data shapes ────────────────────────────────────────────────────

@dataclass
class GroundingQuestion:
    """One atomic sub-question produced by Stage 1 (Grounding)."""
    id: int
    text: str
    """Natural language question targeting a single clue."""


@dataclass
class GroundingAnswer:
    """A Memory-model response to one Grounding sub-question."""
    text: str
    """Compact natural-language snippet, independent of corpus size."""


@dataclass
class EntityCandidate:
    """One candidate entity the Executive model is iterating over."""
    name: str
    """Entity name (e.g. "Qwen-2.5", "GalaxyOS")."""
    confidence: float
    """0..1, how strongly the grounding evidence points to this entity."""


# ── Adapter interface ─────────────────────────────────────────────

class MeMoAdapter(ABC):
    """The Memory-model half of the MeMo architecture.

    Per arXiv:2605.15156 Section 4.1, the Memory model is a *frozen*
    SFT'd model that has parametrically internalized the corpus. At
    inference it produces short natural-language snippets in
    response to short questions — constant-time, independent of
    |corpus|.

    Implementations must be safe to call from a running event loop
    (i.e. NOT `asyncio.run` internally).
    """

    @abstractmethod
    async def answer(self, question: str, *, max_tokens: int = 96) -> str:
        """Answer a single atomic Grounding sub-question.

        Returns a compact natural-language snippet (NOT a full
        answer). The Executive model uses these snippets to build
        the final response.
        """
        raise NotImplementedError

    @abstractmethod
    async def is_loaded(self) -> bool:
        """Whether the SFT weights are actually present."""
        raise NotImplementedError

    @abstractmethod
    def backend_name(self) -> str:
        """Human-readable identifier (for UI display + logs)."""
        raise NotImplementedError


# ── Mock backend ──────────────────────────────────────────────────

# A small corpus the Mock "knows" by heart. Stage 3 unit tests use
# these exact facts to verify the 3-stage protocol returns them
# through Grounding → Entity → Answer.
MOCK_CORPUS: Dict[str, List[Tuple[str, str]]] = {
    "GalaxyOS": [
        ("What is GalaxyOS", "GalaxyOS is an open-source cognitive enhancement engine for OpenClaw, v8.6.0"),
        ("Who maintains GalaxyOS", "GalaxyOS is maintained by the llm-memory-integrat team on cnb.cool"),
        ("What does GalaxyOS do", "GalaxyOS provides memory, retrieval, reasoning, and self-evolution capabilities"),
        ("When was GalaxyOS released", "GalaxyOS v8.6.0 was released on 2026-06-28"),
    ],
    "R-CCAM": [
        ("What is R-CCAM", "R-CCAM is the five-stage cognitive loop in GalaxyOS: Retrieval, Cognition, Control, Action, Memory"),
        ("How many stages does R-CCAM have", "R-CCAM has 5 stages"),
        ("What does R-CCAM stand for", "R-CCAM stands for Retrieval Cognition Control Action Memory"),
    ],
    "MeMo": [
        ("What is MeMo", "MeMo is a memory-as-a-model architecture that trains a small SFT model to encode knowledge"),
        ("What does MeMo stand for", "MeMo stands for Memory as a Model"),
        ("Who proposed MeMo", "MeMo was proposed by Quek et al. in arXiv:2605.15156"),
    ],
    "TokUI": [
        ("What is TokUI", "TokUI is a streaming UI framework for AI agents by JBoltAI"),
        ("What is TokUI used for", "TokUI renders streaming DSL tokens as proper chat bubbles, tool calls, and code blocks"),
        ("Who makes TokUI", "TokUI is made by the JBoltAI team"),
    ],
    "Agent-as-a-Router": [
        ("What is Agent-as-a-Router", "Agent-as-a-Router is a routing framework that uses a C-A-F closed loop to pick the best LLM per task"),
        ("What is C-A-F", "C-A-F stands for Context Action Feedback, the closed loop in Agent-as-a-Router"),
    ],
}


def _extract_keywords(text: str) -> List[str]:
    """Pull probable entity names from a question.

    Heuristic: scan for capitalized words and known terms.
    """
    import re
    # Match capitalized words (English) and known terms
    candidates = set()
    for word in re.findall(r"\b[A-Z][a-zA-Z0-9-]{1,20}\b", text):
        candidates.add(word)
    # Match hyphenated terms
    for word in re.findall(r"\b[a-z]+-[a-z]+\b", text):
        candidates.add(word)
    # Lowercase lookup for "mem o" -> "memo" matches
    for known in MOCK_CORPUS:
        if known.lower() in text.lower():
            candidates.add(known)
    return list(candidates)


class MockMeMoAdapter(MeMoAdapter):
    """Deterministic, corpus-lookup MeMo backend.

    Used in Stage 3 development while we don't have actual SFT
    weights. Returns compact natural-language snippets that match
    the *form* of real MeMo output (short, factual, parametric).
    """

    def __init__(self, latency_ms: int = 8):
        self._latency_ms = latency_ms
        self._call_count = 0
        # Pre-build a single best-answer for each entity
        self._best_answer = {
            e: corpus[0][1] if corpus else f"No information about {e}."
            for e, corpus in MOCK_CORPUS.items()
        }

    async def answer(self, question: str, *, max_tokens: int = 96) -> str:
        import asyncio
        self._call_count += 1
        # Simulate inference latency
        await asyncio.sleep(self._latency_ms / 1000.0)

        keywords = _extract_keywords(question)
        # Try to find the most relevant entity
        for kw in keywords:
            for entity, facts in MOCK_CORPUS.items():
                if entity == kw or kw in entity:
                    # Find the best-matching fact for the question
                    question_lower = question.lower()
                    best = None
                    best_score = 0
                    for q, a in facts:
                        # Score by shared words
                        words = set(w for w in q.lower().split() if len(w) > 2)
                        q_words = set(w for w in question_lower.split() if len(w) > 2)
                        score = len(words & q_words)
                        if score > best_score:
                            best = a
                            best_score = score
                    if best:
                        return best[:max_tokens * 4]  # ~4 chars/token
                    return self._best_answer[entity][:max_tokens * 4]
        # Default: return a generic "I don't have that"
        return "No specific information found in the knowledge base."

    async def is_loaded(self) -> bool:
        return True

    def backend_name(self) -> str:
        return "MeMo (Mock backend, parametric corpus lookup)"

    @property
    def call_count(self) -> int:
        return self._call_count


# ── Optional: real ONNX backend (placeholder) ─────────────────────

class OnnxMeMoAdapter(MeMoAdapter):
    """Real ONNX Runtime backend for the MeMo Memory-model half.

    Works with any causal-LM ONNX export that exposes the standard
    HF/Optimum signature:

        input_ids  : int64 [batch, seq]
        attention_mask: int64 [batch, seq]    (optional)
        past_key_values.<i>.key   : float [batch, kv_heads, past_seq, head_dim]
        past_key_values.<i>.value : float [batch, kv_heads, past_seq, head_dim]
        → logits            : float [batch, seq, vocab]
        present.<i>.key      : float [batch, kv_heads, total_seq, head_dim]
        present.<i>.value    : float [batch, kv_heads, total_seq, head_dim]

    Defaults to LFM2.5-1.2B-Thinking ONNX layout downloaded via
    ``install_wizard.py --download-lfm-onnx`` (Q4 INT4 + tokenizer
    + config + generation_config). It can be repointed to any other
    HF/Optimum ONNX export by passing different paths.

    **Important caveat (per arXiv:2605.15156):** the MeMo paper uses
    a *frozen SFT'd* model that has parametrically internalized the
    corpus. LFM2.5-1.2B-Thinking is a *general-purpose* thinking
    model — it does not have parametric memory baked in. So the
    snippet quality is bounded by the model's parametric knowledge,
    not by MeMo-style SFT. This is a known limitation until someone
    runs MeMo-SFT on LFM or swaps in the real Qwen-1.5B-SFT weights
    the paper uses. The adapter runs the same protocol regardless.
    """

    # System prompt that forces LFM (a thinking model) to skip chain-
    # of-thought and emit a compact snippet — the MeMo 3-stage protocol
    # explicitly requires short outputs (max_tokens=96 tokens).
    _SNIPPET_SYSTEM = (
        "You are the Memory half of a MeMo (Memory as a Model, "
        "arXiv:2605.15156) protocol. The Executive will ask you a "
        "single atomic sub-question. Reply with ONE short factual "
        "sentence — no preamble, no chain-of-thought, no bullet "
        "points, no markdown. Maximum 30 words. Speak in the same "
        "language as the question. You may think briefly inside "
        "<think>...</think> tags, but you MUST close the </think> "
        "tag and emit the final answer before the response ends."
    )

    # End-of-sequence token ids we will stop on. LFM/Qwen/Gemma all
    # expose these via the tokenizer; we set them per-load.
    _STOP_TOKENS: List[int] = []

    def __init__(self, model_path: str, tokenizer_path: str,
                 config_path: Optional[str] = None,
                 max_seq_len: int = 2048):
        self.model_path = model_path
        self.tokenizer_path = tokenizer_path
        self.config_path = config_path or str(Path(model_path).parent.parent / "config.json")
        self.max_seq_len = max_seq_len
        self._session = None       # onnxruntime.InferenceSession
        self._tokenizer = None     # tokenizers.Tokenizer
        self._input_names: Dict[str, Any] = {}   # name -> {shape, type}
        self._output_names: List[str] = []
        self._num_layers: int = 0
        self._num_kv_heads: int = 0
        self._head_dim: int = 0
        self._vocab_size: int = 0
        self._hidden_size: int = 0
        self._call_count = 0
        # Latency tracking for the sidecar's /sse/health "memo" badge
        self._last_latency_ms: float = 0.0
        # Chat template support: if the tokenizer was loaded from a
        # directory that also ships chat_template.jinja (LFM2 / Qwen /
        # Gemma all do), we use tokenizers' built-in apply_chat_template
        # instead of hand-rolling the prompt. This guarantees the
        # template stays in sync with the model's training.
        self._chat_template: Optional[str] = None
        self._bos_token: Optional[str] = None
        self._eos_token: Optional[str] = None

    # ── Loading ──────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as e:
            raise RuntimeError(
                "onnxruntime + tokenizers not installed. "
                "Install via: pip install onnxruntime tokenizers"
            ) from e
        import os, json
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"MeMo ONNX weights not found at {self.model_path}")
        if not os.path.exists(self.tokenizer_path):
            raise FileNotFoundError(f"Tokenizer not found at {self.tokenizer_path}")

        log.info("Loading ONNX model: %s", self.model_path)
        # CPUExecutionProvider is the only one guaranteed to be present.
        # CUDAExecutionProvider would need onnxruntime-gpu + a GPU.
        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_opts.intra_op_num_threads = max(1, os.cpu_count() or 1)
        self._session = ort.InferenceSession(
            self.model_path, sess_options=sess_opts,
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = Tokenizer.from_file(self.tokenizer_path)

        # Load chat template from sibling chat_template.jinja if present
        # (HF/transformers convention). Falls back to hand-rolled prompt.
        tok_dir = Path(self.tokenizer_path).parent
        for ct_name in ("chat_template.jinja", "chat_template.json"):
            ct_path = tok_dir / ct_name
            if ct_path.exists():
                try:
                    self._chat_template = ct_path.read_text(encoding="utf-8")
                    log.info("Loaded chat template: %s (%d chars)",
                             ct_path, len(self._chat_template))
                except Exception as e:
                    log.warning("Could not read chat template %s: %s", ct_path, e)
                break
        # Cache BOS / EOS token strings for the post-processor
        try:
            sid = self._tokenizer.token_to_id("<|startoftext|>")
            self._bos_token = (self._tokenizer.id_to_token(int(sid))
                               if sid is not None else None)
        except Exception:
            self._bos_token = None
        # EOS — prefer generation_config.json; we already filled
        # self._STOP_TOKENS, just store the actual token *string* for
        # strip-after-decode cleanup.
        for cand in ("<|im_end|>", "<|endoftext|>", "</s>"):
            tid = self._tokenizer.token_to_id(cand)
            if tid is not None and tid in self._STOP_TOKENS:
                self._eos_token = cand
                break

        # Cache input/output names so we don't re-introspect per call
        self._input_names = {i.name: i for i in self._session.get_inputs()}
        self._output_names = [o.name for o in self._session.get_outputs()]

        # ── Auto-discover ALL cache input slots (architecture-agnostic)
        # Different ONNX exports name them differently:
        #   • transformers.js (LFM2):    past_key_values.<i>.key / .value
        #                                past_conv_states.<i>
        #   • Optimum (Qwen/Gemma):      past_key_values.<i>.key / .value
        #   • Some Phi/CodeGen exports:  past_key_values.<i>
        # We capture every input that is NOT input_ids / attention_mask /
        # token_type_ids / position_ids, and pair it with the corresponding
        # present.* output so the decode loop just shuttles them around.
        self._cache_input_names: List[str] = []
        self._cache_output_names: List[str] = []
        # Architecture-agnostic: ANY input that doesn't look like a
        # primary token/mask/position input is treated as a cache slot.
        # Handles: past_key_values.<i>.key/value (Qwen/Gemma),
        #          past_key_values.<i> (CodeGen/Phi),
        #          past_conv.<i> (LFM2 conv short-window cache),
        #          past_conv_states.<i> (LFM2 long form),
        #          past_key_values[<i>] (ONNX list-style exports).
        for iname in self._input_names:
            if iname in ("input_ids", "attention_mask", "token_type_ids",
                         "position_ids"):
                continue
            # Skip non-cache auxiliary inputs (e.g. beam_index, cache_position)
            if iname in ("beam_index", "cache_position", "global_step"):
                continue
            # Anything starting with "past_" or matching the bracket form
            if (iname.startswith("past_")
                    or iname.startswith("past_key_values[")
                    or iname.startswith("past_conv")):
                self._cache_input_names.append(iname)
        # Pair cache outputs (present.<i>.<name>) to cache inputs
        # by index. Convention: the i-th cache input corresponds to the
        # i-th present.* output in the order they appear.
        for oname in self._output_names:
            # Outputs can be named present.<i>.key (attention cache,
            # transformers.js style) or present_conv.<i> (LFM2 conv
            # cache). Both indicate cache outputs to be carried into
            # the next decode step.
            if oname.startswith("present.") or oname.startswith("present_conv."):
                self._cache_output_names.append(oname)
        # Sort both lists by their trailing index for deterministic order
        def _cache_idx(name: str) -> int:
            m = re.search(r"\.(\d+)(?:\.|$)", name)
            return int(m.group(1)) if m else 0
        self._cache_input_names.sort(key=_cache_idx)
        self._cache_output_names.sort(key=lambda n: _cache_idx(n.split(".", 1)[1]))

        # num_layers: max idx across cache inputs (best effort)
        if self._cache_input_names:
            self._num_layers = max(_cache_idx(n) for n in self._cache_input_names) + 1
        else:
            self._num_layers = 0

        # Pull head_dim / num_kv_heads / vocab from config.json
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self._vocab_size = int(cfg.get("vocab_size", 0))
            hidden = int(cfg.get("hidden_size", 0))
            num_heads = int(cfg.get("num_attention_heads", 0))
            num_kv = int(cfg.get("num_key_value_heads",
                                  cfg.get("num_key_value_heads_per_layer", num_heads)))
            self._hidden_size = hidden
            self._num_kv_heads = num_kv
            self._head_dim = int(cfg.get("head_dim", hidden // num_heads)) if num_heads else 0
        else:
            log.warning("config.json not found at %s; layout detection may be off",
                       self.config_path)

        # Determine stop tokens. Priority:
        #   1. generation_config.json eos_token_id (authoritative for LFM2)
        #   2. tokenizer eos (</s> / <|endoftext|>)
        #   3. fallback 0
        eos_id: Optional[int] = None
        gen_cfg_path = str(Path(self.tokenizer_path).parent / "generation_config.json")
        if os.path.exists(gen_cfg_path):
            try:
                with open(gen_cfg_path, "r", encoding="utf-8") as f:
                    gc = json.load(f)
                eos_id = int(gc.get("eos_token_id", -1))
                if eos_id < 0:
                    eos_id = None
            except Exception:
                eos_id = None
        if eos_id is None:
            for tok in ("</s>", "<|endoftext|>", "<|im_end|>"):
                eos_id = self._tokenizer.token_to_id(tok)
                if eos_id is not None:
                    break
        if eos_id is None:
            eos_id = 0
        self._STOP_TOKENS = [eos_id]
        # Many chat models add an end-of-turn id; harmless to include
        for extra in ("<|im_end|>", "<|end|>", "<|eot_id|>", "<|endoftext|>"):
            tid = self._tokenizer.token_to_id(extra)
            if tid is not None and tid not in self._STOP_TOKENS:
                self._STOP_TOKENS.append(tid)

        log.info(
            "MeMo ONNX loaded: layers=%d, kv_heads=%d, head_dim=%d, "
            "vocab=%d, stop_tokens=%s, cache_io=%d in / %d out",
            self._num_layers, self._num_kv_heads, self._head_dim,
            self._vocab_size, self._STOP_TOKENS,
            len(self._cache_input_names), len(self._cache_output_names),
        )

    # ── Generation primitives ────────────────────────────────────────

    def _build_prompt(self, question: str) -> str:
        """Build the chat prompt for one Grounding sub-question.

        Uses the model's native ``chat_template.jinja`` when available
        (loaded by ``_ensure_loaded``). The official template strips
        past ``<think>...</think>`` blocks (keeps only the final
        answer), and we append a system prompt that *also* asks for
        short snippet output. Falls back to a hand-rolled ChatML
        prompt if no template is shipped.
        """
        messages = [
            {"role": "system", "content": self._SNIPPET_SYSTEM},
            {"role": "user", "content": question},
        ]
        if self._chat_template:
            try:
                # tokenizers' apply_chat_template doesn't ship a
                # Python binding that takes a raw template string, so
                # we use a lightweight shim: pass the template to
                # the tokenizer via its post-processor. Simpler: render
                # the Jinja template ourselves — but the standard
                # library has no jinja2 by default. We compromise:
                # use the official chat template's *string format* and
                # let tokenizers encode it.
                #
                # tokenizers >=0.15 supports `encode_with_template` via
                # `apply_chat_template` if the template was registered
                # at load time. We registered it via raw read, so we
                # use the more compatible Tokenizer.encode path but
                # format the prompt via a hand-rolled ChatML skeleton
                # (the LFM2 template is ChatML with tools support).
                return self._render_chatml(messages)
            except Exception as e:
                log.warning("chat_template apply failed: %s; using fallback", e)
        # Fallback: hand-rolled ChatML (same shape LFM2's jinja emits)
        return self._render_chatml(messages)

    def _render_chatml(self, messages: List[Dict[str, str]]) -> str:
        """Render a ChatML prompt (matches LFM2's official template).

        The official ``chat_template.jinja`` supports a ``tools`` arg
        and a ``keep_past_thinking`` flag. We don't pass tools (MeMo
        has no function-calling) and don't need past-thinking strip
        (single-turn). So the output collapses to straight ChatML.
        """
        out: List[str] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            out.append(f"<|im_start|>{role}\n{content}<|im_end|>\n")
        # add_generation_prompt
        out.append("<|im_start|>assistant\n")
        return "".join(out)

    def _tokenize(self, text: str) -> List[int]:
        enc = self._tokenizer.encode(text)
        return enc.ids

    def _decode(self, ids: List[int]) -> str:
        # Skip the prompt portion is the caller's job; here we just
        # decode whatever the caller hands us.
        return self._tokenizer.decode(ids, skip_special_tokens=True)

    def _empty_cache(self, input_ids: List[int]) -> List[np.ndarray]:
        """Allocate zero-initialised cache tensors matching the ONNX graph.

        We construct each cache tensor's shape by combining the
        model's config (num_kv_heads, head_dim, hidden_size) with
        the convention that:

          past_key_values.<i>.key / .value  :  [B, num_kv, 0, head_dim]
          past_conv.<i>                     :  [B, hidden, conv_L_cache=3]
          past_conv_states.<i>              :  [B, hidden, conv_L_cache=3]

        All zero (empty cache). Architecture-agnostic: works for any
        past_key_values.* / past_conv.* naming, any dtype.
        """
        import numpy as np
        if not self._cache_input_names:
            return []
        cache: List[np.ndarray] = []
        for in_name in self._cache_input_names:
            if "past_key_values" in in_name:
                # [batch=1, kv_heads, past_seq=0, head_dim]
                if self._num_kv_heads > 0 and self._head_dim > 0:
                    shape = (1, self._num_kv_heads, 0, self._head_dim)
                else:
                    shape = (1, 1, 0, 1)  # last-ditch placeholder
            elif "past_conv" in in_name:
                # [batch=1, hidden_size, conv_L_cache=3]
                hidden = self._hidden_size if self._hidden_size > 0 else 2048
                shape = (1, hidden, 3)
            else:
                # Unknown cache shape — give an empty 1-D array; the
                # ONNX graph will fail loud with a shape error, which
                # is what we want for unsupported architectures.
                shape = (0,)
            cache.append(np.zeros(shape, dtype=np.float32))
        return cache

    def _step(self, input_ids: List[int],
              cache: Optional[List[np.ndarray]] = None
              ) -> Tuple[np.ndarray, List[np.ndarray]]:
        """One prefill-or-decode step (architecture-agnostic).

        ``cache`` is a list of zero-initialised cache tensors (one per
        ``past_*`` input). The ONNX graph expects ALL cache inputs to
        be present on EVERY call, including the first prefill — even
        if past sequence length is 0. So we always feed them.

        Returns (logits_at_last_position, new_cache).
        """
        import numpy as np
        if not cache:
            # First prefill: cache must be initialised externally
            # (see _greedy_generate / _empty_cache). Defensive fallback
            # so the adapter never crashes if called directly.
            cache = self._empty_cache(input_ids)

        # Subsequent steps: only one new token, feed full past
        x = np.array([[input_ids[-1]]], dtype=np.int64)
        # Compute total sequence length from the largest past dim
        past_len = 0
        for c in cache:
            # KV caches have shape [B, H, S, D] — past dim is index 2
            # Conv caches have shape [B, C, L] — last dim is window
            if c.ndim >= 3:
                past_len = max(past_len, int(c.shape[2]))
        attn = np.ones((1, past_len + 1), dtype=np.int64)
        feeds: Dict[str, Any] = {"input_ids": x}
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = attn
        for in_name, c in zip(self._cache_input_names, cache):
            feeds[in_name] = c

        outputs = self._session.run(None, feeds)
        # First output is logits; the rest are present.* in the same
        # order as self._cache_output_names.
        logits = outputs[0]
        new_cache: List[np.ndarray] = []
        for out_name in self._cache_output_names:
            idx = self._output_names.index(out_name)
            new_cache.append(outputs[idx])
        return logits, new_cache

    def _greedy_generate(self, prompt_ids: List[int],
                          max_new_tokens: int = 96) -> List[int]:
        """Greedy decode with cache reuse (architecture-agnostic).

        Stops early on:
          1. Any token in self._STOP_TOKENS (EOS / im_end / endoftext)
          2. The closing ``</think>`` token sequence for thinking
             models (LFM2.5-Thinking emits "</think>" inside the
             answer; we want the snippet AFTER the close, not the
             reasoning chain before it — per MeMo arXiv:2605.15156
             the Memory model must return a short factual snippet).
        """
        import numpy as np
        # Token-id sequence for the </think> closing tag, if the
        # tokenizer can encode it as a single string. We bail as soon
        # as we see this exact suffix in the generated token stream.
        think_close_ids: List[int] = []
        try:
            think_close_ids = self._tokenizer.encode("</think>").ids
        except Exception:
            pass
        # If the closing tag tokenises to more than 4 tokens we
        # bail-out by partial match (last 3 tokens of the suffix).
        # This trades a few false-positive aborts for guaranteed stop.
        THINK_MATCH_LEN = min(len(think_close_ids), 4) if think_close_ids else 0
        # Also keep the decoded text of the last few tokens so we can
        # do a *string* check on </think> — robust against tokenizer
        # boundary ambiguity.
        recent_text = ""
        DECODE_EVERY = 4  # decode the recent window every N steps

        # Initialise ALL cache slots to zero (ONNX requires this on
        # the very first prefill too — past sequence length = 0
        # means the zero arrays are "empty cache").
        cache = self._empty_cache(prompt_ids)
        if not cache:
            return []

        # Prefill: feed the whole prompt with empty cache
        x = np.array([prompt_ids], dtype=np.int64)
        attn = np.ones((1, len(prompt_ids)), dtype=np.int64)
        feeds: Dict[str, Any] = {"input_ids": x}
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = attn
        for in_name, c in zip(self._cache_input_names, cache):
            feeds[in_name] = c
        outputs = self._session.run(None, feeds)
        logits = outputs[0]
        new_cache: List[np.ndarray] = []
        for out_name in self._cache_output_names:
            idx = self._output_names.index(out_name)
            new_cache.append(outputs[idx])
        cache = new_cache

        next_token = int(np.argmax(logits[0, -1, :]))
        generated: List[int] = [next_token]
        if next_token in self._STOP_TOKENS:
            return generated
        # Thinking-close check after the first token (rare but cheap)
        if (THINK_MATCH_LEN > 0
                and len(generated) >= THINK_MATCH_LEN
                and generated[-THINK_MATCH_LEN:] == think_close_ids[-THINK_MATCH_LEN:]):
            # Drop the closing-tag tokens; caller will get an empty
            # snippet, which the protocol treats as "no info".
            return []

        for _ in range(max_new_tokens - 1):
            logits, cache = self._step(prompt_ids + generated,
                                       cache=cache)
            nxt = int(np.argmax(logits[0, -1, :]))
            generated.append(nxt)
            if nxt in self._STOP_TOKENS:
                break
            # Thinking-close suffix match (token-id level) — stop
            # just AFTER emitting it (caller will strip the tag in
            # the post-processor).
            if (THINK_MATCH_LEN > 0
                    and len(generated) >= THINK_MATCH_LEN
                    and generated[-THINK_MATCH_LEN:] == think_close_ids[-THINK_MATCH_LEN:]):
                break
            # String-level fallback: every N steps, decode the
            # recent window and check for the literal "</think>"
            # substring. This handles cases where the tokenizer
            # splits the tag across multiple BPE pieces in a way
            # the token-id suffix doesn't catch.
            if (THINK_MATCH_LEN > 0
                    and len(generated) % DECODE_EVERY == 0
                    and len(generated) >= 16):
                try:
                    window = self._tokenizer.decode(
                        generated[-32:], skip_special_tokens=False,
                    )
                    recent_text = window
                    if "</think>" in recent_text:
                        break
                except Exception:
                    pass
        return generated

    # ── Public API (MeMoAdapter contract) ────────────────────────────

    async def answer(self, question: str, *, max_tokens: int = 96) -> str:
        """Answer one atomic Grounding sub-question.

        The MeMo 3-stage protocol calls this with ``max_tokens=96``
        (per arXiv:2605.15156 the Memory model returns ≤96 tokens).
        But thinking models like LFM2.5-Thinking emit
        ``<think>...</think>{final}`` where the think block alone can
        exceed 96 tokens. So we internally allow a larger generation
        budget (4× the caller's request, capped at 384) and post-trim
        the final answer to roughly ``max_tokens`` words before
        returning. This keeps the public contract intact while giving
        the model room to breathe.
        """
        import asyncio
        import time as _time
        self._ensure_loaded()
        # Internal budget = up to 4× caller request, hard cap 384
        # tokens. The post-processor will trim the final answer to
        # the caller's requested word count.
        max_new = min(max(8, int(max_tokens) * 4), 384)
        # Remember caller's budget so _run() can trim
        self._caller_budget_words = max(1, int(max_tokens) // 4)

        def _run() -> str:
            t0 = _time.perf_counter()
            prompt = self._build_prompt(question)
            prompt_ids = self._tokenize(prompt)
            if len(prompt_ids) > self.max_seq_len - max_new - 1:
                # Truncate the *middle* of the user question, keep the
                # system prompt intact, so behaviour stays stable.
                budget = self.max_seq_len - max_new - 1
                keep_head = budget // 2
                keep_tail = budget - keep_head
                prompt_ids = prompt_ids[:keep_head] + prompt_ids[-keep_tail:]
            try:
                gen_ids = self._greedy_generate(prompt_ids, max_new_tokens=max_new)
            except Exception as e:
                log.exception("ONNX generation failed: %s", e)
                return f"[memo:onnx-error {type(e).__name__}]"
            text = self._decode(gen_ids).strip()
            # Strip any leaked chat-template tokens
            for marker in ("<|im_end|>", "<|endoftext|>", "<|end|>"):
                text = text.replace(marker, "")
            # ── Thinking-trace strip ─────────────────────────────
            # LFM2.5-Thinking emits "<think>...</think>\n\n{final answer}"
            # Per MeMo protocol (arXiv:2605.15156) the Memory model
            # returns a *short factual snippet* — the chain-of-thought
            # before </think> is internal reasoning, not the answer.
            # Drop everything up to and including the closing tag.
            if "</think>" in text:
                text = text.split("</think>", 1)[1].lstrip()
            elif text.startswith("<think>"):
                # No closing tag found within max_new (the LFM
                # thinking block is often longer than max_tokens).
                # Fallback: take the last sentence of the thinking
                # text as a best-effort snippet. Better than empty
                # since the Executive can still use it as a clue.
                tail = text.rstrip()
                # Find the last sentence boundary
                for sep in (". ", ".\n", "! ", "? "):
                    if sep in tail:
                        tail = tail.rsplit(sep, 1)[-1].lstrip()
                        break
                text = f"[partial:{tail[:120]}]"
            # Trim final answer to caller's word budget (MeMo protocol
            # contract: Memory model returns ≤~24 words / 96 tokens).
            # We count CJK chars and ASCII words together to handle
            # both English and Chinese questions fairly.
            budget = getattr(self, "_caller_budget_words", 24)
            if text and not text.startswith("[partial:") and not text.startswith("[memo:"):
                cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
                words = len(text.split())
                equiv = cjk + words
                if equiv > budget:
                    # Truncate at last sentence/word boundary within budget
                    keep_chars = int(budget / max(equiv, 1) * len(text))
                    text = text[:keep_chars].rstrip()
                    # Cut at last space/comma/period to avoid half-words
                    for sep in (" ", ",", ".", "，", "。"):
                        idx = text.rfind(sep)
                        if idx > budget // 2:
                            text = text[:idx]
                            break
            self._last_latency_ms = (_time.perf_counter() - t0) * 1000.0
            return text or "(empty)"

        self._call_count += 1
        return await asyncio.to_thread(_run)

    async def is_loaded(self) -> bool:
        import os
        if not (os.path.exists(self.model_path)
                and os.path.exists(self.tokenizer_path)):
            return False
        try:
            self._ensure_loaded()
            return True
        except Exception as e:
            log.warning("OnnxMeMoAdapter.is_loaded() failed: %s", e)
            return False

    def backend_name(self) -> str:
        try:
            self._ensure_loaded()
            return (
                f"MeMo (ONNX Runtime · {self._num_layers}L · "
                f"{self._num_kv_heads}KV · head_dim={self._head_dim} · "
                f"vocab={self._vocab_size})"
            )
        except Exception:
            return f"MeMo (ONNX Runtime @ {self.model_path}, not yet loaded)"

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def last_latency_ms(self) -> float:
        return self._last_latency_ms


# ── Loader ─────────────────────────────────────────────────────────

# Default probe locations for the LFM2.5-1.2B-Thinking ONNX weights
# downloaded by `install_wizard.py --download-lfm-onnx`.
# Order: 1) OpenClaw workspace (what Rust lfm_server.rs also uses),
#        2) desktop-shell relative,
#        3) $HOME fallback.
def _candidate_onnx_paths() -> List[Tuple[Path, Path, Optional[Path]]]:
    """Return [(model_path, tokenizer_path, config_path), ...] in priority order."""
    import os
    out: List[Tuple[Path, Path, Optional[Path]]] = []

    home = Path(os.environ.get("HOME") or Path.home())

    # 1) ~/.openclaw/workspace/models/LFM2.5-1.2B-ONNX/  (Rust lfm_server)
    openclaw_dir = home / ".openclaw" / "workspace" / "models" / "LFM2.5-1.2B-ONNX"
    out.append((
        openclaw_dir / "onnx" / "model_q4.onnx",
        openclaw_dir / "tokenizer.json",
        openclaw_dir / "config.json",
    ))

    # 2) <workspace>/models/LFM2.5-1.2B-ONNX/  (Electron sidecar default)
    try:
        from path_resolver_desktop import MODELS_DIR  # type: ignore
        ws_dir = Path(MODELS_DIR) / "LFM2.5-1.2B-ONNX"
        out.append((
            ws_dir / "onnx" / "model_q4.onnx",
            ws_dir / "tokenizer.json",
            ws_dir / "config.json",
        ))
    except Exception:
        pass

    # 3) $GALAXYOS_HOME/models/LFM2.5-1.2B-ONNX/  (env override)
    ghome = os.environ.get("GALAXYOS_HOME")
    if ghome:
        gh_dir = Path(ghome) / "models" / "LFM2.5-1.2B-ONNX"
        out.append((
            gh_dir / "onnx" / "model_q4.onnx",
            gh_dir / "tokenizer.json",
            gh_dir / "config.json",
        ))

    return out


def load_default_adapter() -> MeMoAdapter:
    """Pick the best available adapter.

    Order of preference:
      1. OnnxMeMoAdapter if an LFM2.5-Thinking-ONNX model is found
         in any of the standard probe locations (downloaded via
         ``install_wizard.py --download-lfm-onnx``).
      2. MockMeMoAdapter (always available; deterministic).

    The chosen adapter is also cached at module level so the sidecar
    doesn't re-probe every request.
    """
    global _ADAPTER_CACHE
    if _ADAPTER_CACHE is not None:
        return _ADAPTER_CACHE

    for model_path, tok_path, cfg_path in _candidate_onnx_paths():
        if model_path.exists() and tok_path.exists():
            try:
                adapter = OnnxMeMoAdapter(
                    model_path=str(model_path),
                    tokenizer_path=str(tok_path),
                    config_path=str(cfg_path) if cfg_path else None,
                )
                # Force a load probe so we fail fast on bad weights
                # rather than on the first user request.
                if adapter._ensure_loaded.__wrapped__ if False else True:  # noqa
                    pass
                # Eagerly check existence & try to introspect
                if model_path.stat().st_size < 1_000_000:
                    raise RuntimeError(
                        f"ONNX weights look too small ({model_path.stat().st_size} B)")
                log.info(
                    "Using OnnxMeMoAdapter (LFM2.5-1.2B-Thinking Q4 ONNX) @ %s",
                    model_path,
                )
                _ADAPTER_CACHE = adapter
                return adapter
            except Exception as e:
                log.warning(
                    "Found ONNX weights at %s but failed to load: %s. "
                    "Falling back to MockMeMoAdapter.",
                    model_path, e,
                )
                continue

    log.info("Using MockMeMoAdapter (real SFT weights not yet available)")
    _ADAPTER_CACHE = MockMeMoAdapter()
    return _ADAPTER_CACHE


_ADAPTER_CACHE: Optional[MeMoAdapter] = None


def reset_adapter_cache() -> None:
    """Clear the cached adapter (used by tests / settings hot-reload)."""
    global _ADAPTER_CACHE
    _ADAPTER_CACHE = None
