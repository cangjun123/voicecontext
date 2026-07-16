from __future__ import annotations

from voice_context.asr.funasr_engine import FunASREngine


def _model_dir(root, name):
    directory = root / name
    directory.mkdir()
    (directory / "model_quant.onnx").write_bytes(b"model")
    return directory


def test_parent_modelscope_directory_is_resolved(tmp_path) -> None:
    zh = _model_dir(tmp_path, "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx")
    _model_dir(tmp_path, "speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx")
    en = _model_dir(tmp_path, "speech_paraformer-large_asr_nat-en-16k-common-vocab10020-onnx")
    vad = _model_dir(tmp_path, "speech_fsmn_vad_zh-cn-16k-common-onnx")
    punc = _model_dir(tmp_path, "punc_ct-transformer_zh-cn-common-vocab272727-onnx")

    engine = FunASREngine({"model_path": str(tmp_path), "language": "zh"})
    assert engine._resolve_onnx_paths() == (zh, vad, punc)
    engine = FunASREngine({"model_path": str(tmp_path), "language": "en"})
    assert engine._resolve_onnx_paths()[0] == en


def test_onnx_result_and_punctuation_are_normalized(tmp_path) -> None:
    engine = FunASREngine({"language": "zh"})
    engine._backend = "onnx"
    engine._model = lambda audio: [{"preds": ("修复逻辑问题", ["修", "复"])}]
    engine._punc_model = lambda text: (text + "。", [1, 3])
    result = engine.transcribe(tmp_path / "unused.wav")
    assert result.text == "修复逻辑问题。"
    assert result.language == "zh"
