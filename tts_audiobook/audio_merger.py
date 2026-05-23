"""音频拼接：将多个 WAV 片段合并为完整有声书。"""

import io
import struct
import wave
from typing import Optional

from .config import AUDIO_SAMPLE_RATE, AUDIO_CHANNELS, AUDIO_BIT_DEPTH


def merge_wavs(
    wav_data_list: list[bytes],
    silence_sec: float = 0.3,
    sample_rate: int = AUDIO_SAMPLE_RATE,
    channels: int = AUDIO_CHANNELS,
    bit_depth: int = AUDIO_BIT_DEPTH,
) -> bytes:
    """将多个 WAV 片段拼接，块间插入静音。

    Args:
        wav_data_list: WAV 字节数据列表
        silence_sec: 块间静音时长（秒）
        sample_rate: 采样率
        channels: 声道数
        bit_depth: 位深度

    Returns:
        合并后的 WAV 文件字节
    """
    if not wav_data_list:
        raise ValueError("没有可合并的音频数据")

    sample_width = bit_depth // 8

    # 解码所有片段为 PCM 采样数据
    all_samples: list[bytes] = []
    silence_frames = _make_silence(silence_sec, sample_rate, channels, sample_width)

    for i, data in enumerate(wav_data_list):
        pcm = _decode_wav_to_pcm(data)
        if pcm:
            all_samples.append(pcm)
            # 块间插入静音（最后一块不加）
            if i < len(wav_data_list) - 1 and silence_sec > 0:
                all_samples.append(silence_frames)

    if not all_samples:
        raise ValueError("所有音频片段解码后为空")

    merged_pcm = b"".join(all_samples)
    return _encode_pcm_to_wav(merged_pcm, sample_rate, channels, sample_width)


def _decode_wav_to_pcm(data: bytes) -> Optional[bytes]:
    """从 WAV 字节解码为原始 PCM。"""
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            return wf.readframes(wf.getnframes())
    except Exception:
        return None


def _encode_pcm_to_wav(
    pcm: bytes, sample_rate: int, channels: int, sample_width: int
) -> bytes:
    """将原始 PCM 封装为 WAV 文件。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _make_silence(
    duration_sec: float, sample_rate: int, channels: int, sample_width: int
) -> bytes:
    """生成静音 PCM 数据。"""
    num_frames = int(sample_rate * duration_sec * channels)
    return b"\x00" * (num_frames * sample_width)


def wav_duration_sec(wav_data: bytes) -> float:
    """获取 WAV 音频时长（秒）。"""
    try:
        with wave.open(io.BytesIO(wav_data), "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 0.0


def convert_to_mp3(wav_data: bytes, bitrate: str = "64k") -> bytes:
    """将 WAV 转为 MP3（需要 pydub + ffmpeg）。"""
    try:
        from pydub import AudioSegment
    except ImportError:
        raise ImportError("MP3 转换需要 pydub: pip install pydub")

    seg = AudioSegment.from_wav(io.BytesIO(wav_data))
    buf = io.BytesIO()
    seg.export(buf, format="mp3", bitrate=bitrate)
    return buf.getvalue()
