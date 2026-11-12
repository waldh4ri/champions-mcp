"""Web chat helpers: Hugging Face link parsing, picker values, settings and
the llama.cpp preset file they render to."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docker" / "chat"))

import backends as bk  # noqa: E402
import settings as cfg  # noqa: E402


@pytest.mark.parametrize("text,ref", [
    ("unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL", "unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL"),
    ("unsloth/Qwen3.5-9B-GGUF", "unsloth/Qwen3.5-9B-GGUF"),
    ("https://huggingface.co/unsloth/Qwen3.5-9B-GGUF", "unsloth/Qwen3.5-9B-GGUF"),
    ("https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/", "unsloth/Qwen3.5-9B-GGUF"),
    ("https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/tree/main", "unsloth/Qwen3.5-9B-GGUF"),
    ("https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/blob/main/Qwen3.5-9B-UD-Q4_K_XL.gguf",
     "unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL"),
    ("https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf?download=true",
     "unsloth/Qwen3.5-9B-GGUF:Q4_K_M"),
    ("hf.co/unsloth/Qwen3.5-9B-GGUF:Q8_0", "unsloth/Qwen3.5-9B-GGUF:Q8_0"),
    ("https://huggingface.co/unsloth/Qwen3.5-9B-MTP-GGUF/blob/main/Qwen3.5-9B-UD-Q4_K_XL.gguf",
     "unsloth/Qwen3.5-9B-MTP-GGUF:UD-Q4_K_XL"),
    ("https://huggingface.co/unsloth/Kimi-K2-GGUF/blob/main/UD-IQ1_S/Kimi-K2-UD-IQ1_S-00001-of-00006.gguf",
     "unsloth/Kimi-K2-GGUF:UD-IQ1_S"),
    ("https://huggingface.co/x/Model-GGUF/blob/main/model-bf16.gguf", "x/Model-GGUF:bf16"),
    ("  'unsloth/Qwen3.5-4B-GGUF:UD-Q4_K_XL'  ", "unsloth/Qwen3.5-4B-GGUF:UD-Q4_K_XL"),
])
def test_parse_hf_ref(text, ref):
    assert bk.parse_hf_ref(text) == ref


@pytest.mark.parametrize("bad", ["", "qwen", "https://example.com/model.gguf", "a/b/c d"])
def test_parse_hf_ref_rejects(bad):
    with pytest.raises(ValueError):
        bk.parse_hf_ref(bad)


def test_picker_values_are_provider_qualified():
    value = bk.qualify(bk.LOCAL, "unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL")
    assert bk.split(value) == ("local", "unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL")
    assert bk.split("openrouter::openrouter/auto (low)") == ("openrouter", "openrouter/auto (low)")
    assert bk.split("ep:a1b2c3::gpt-x") == ("ep:a1b2c3", "gpt-x")
    assert bk.split("qwen3.6-35b-a3b") == (None, "qwen3.6-35b-a3b")  # chats saved before


def test_settings_validate_and_inherit(tmp_path):
    s = cfg.Settings(str(tmp_path))
    with pytest.raises(ValueError):
        s.update("inference", {"cache_type_k": "q3_weird"})
    with pytest.raises(ValueError):
        s.update("inference", {"ctx": 10})          # below the minimum
    with pytest.raises(ValueError):
        s.update("chat", {"nope": 1})
    s.update("inference", {"ctx": 65536})
    s.update("inference", {"n_cpu_moe": 18}, model="big/model-GGUF:Q4_K_M")
    assert s.inference("ctx", "big/model-GGUF:Q4_K_M") == 65536   # inherited
    assert s.inference("n_cpu_moe", "big/model-GGUF:Q4_K_M") == 18
    assert s.inference("n_cpu_moe", "other") is None
    s.update("inference", {"ctx": None})                          # reset
    assert s.inference("ctx") is None
    # persisted
    assert cfg.Settings(str(tmp_path)).inference("n_cpu_moe", "big/model-GGUF:Q4_K_M") == 18


def test_presets_render_overrides_and_mtp(tmp_path):
    s = cfg.Settings(str(tmp_path))
    s.update("inference", {"reasoning_budget": 1024})
    s.update("inference", {"n_cpu_moe": 18}, model="big/model-GGUF:Q4_K_M")
    models = [{"id": "big/model-GGUF:Q4_K_M", "sidecars": []},
              {"id": "unsloth/Qwen3.5-9B-MTP-GGUF:UD-Q4_K_XL", "sidecars": ["mtp"]},
              {"id": "plain/model", "sidecars": ["mmproj"]}]
    text = s.render_presets(models)
    assert text.startswith("version = 1")
    glob = text.split("[*]")[1].split("\n[")[0]
    assert "reasoning-budget = 1024" in glob and "cache-type-k = q8_0" in glob
    assert "ctx-size" not in glob                                   # auto-fit: omitted
    assert "[big/model-GGUF:Q4_K_M]\nn-cpu-moe = 18" in text
    assert "[unsloth/Qwen3.5-9B-MTP-GGUF:UD-Q4_K_XL]\nspec-type = draft-mtp" in text
    assert "[plain/model]" not in text                              # nothing to override
    s.update("inference", {"speculative": "off"}, model="unsloth/Qwen3.5-9B-MTP-GGUF:UD-Q4_K_XL")
    assert "draft-mtp" not in s.render_presets(models)
    assert s.write_presets(models) is True
    assert s.write_presets(models) is False                          # unchanged: no rewrite


def test_router_entries_preset_backed_vs_phantom():
    """A model named by one of our preset sections reports source "preset"
    even with its files on disk (only those carry GGUF metadata such as
    context_length); a section with no file behind it is a phantom."""
    lm = bk.LocalModels("http://llama:8080", True, 131072)
    real = lm._entry({"id": "u/M-MTP-GGUF:Q4_K_XL", "status": {"value": "unloaded"}, "source": "preset",
                      "can_remove": False, "context_length": 262144, "sidecars": ["mmproj"]})
    assert real["on_disk"] and not real["phantom"] and real["removable"] and real["preset_backed"]
    assert real["sidecars"] == ["mmproj"]
    ghost = lm._entry({"id": "u/Gone-GGUF:Q4", "status": {"value": "unloaded"}, "source": "preset"})
    assert ghost["phantom"] and not ghost["on_disk"]
    cached = lm._entry({"id": "u/C-GGUF:Q4", "status": {"value": "unloaded"}, "source": "cache",
                        "can_remove": True})
    assert cached["on_disk"] and cached["removable"] and not cached["phantom"]
    downloading = lm._entry({"id": "u/D-GGUF:Q4", "status": {"value": "downloading"}, "source": "cache",
                             "sidecars": ["mtp", "dflash"]})
    assert not downloading["on_disk"] and downloading["sidecars"] == [] and not downloading["phantom"]
