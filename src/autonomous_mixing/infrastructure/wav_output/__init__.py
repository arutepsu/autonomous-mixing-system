"""Exclusive WAV/DOUBLE output of already computed float64 samples into an allocated workspace."""

from .writing import WavOutputError, WavOutputFailureCode, WrittenWav, write_wav_output

__all__ = [
    "WrittenWav",
    "WavOutputFailureCode",
    "WavOutputError",
    "write_wav_output",
]
