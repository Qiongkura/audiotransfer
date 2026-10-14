# RMVPE attribution

`deepunet.py` and `seq.py` are unchanged copies from
[Dream-High/RMVPE](https://github.com/Dream-High/RMVPE), commit
`a6db1cd7d26014aa739383367afd9bab57fc624c`, under the included Apache 2.0 license.

RMVPE: A Robust Model for Vocal Pitch Estimation in Polyphonic Music,
Haojie Wei, Xueke Cao, Tangpeng Dan, Yueguo Chen, Interspeech 2023.
[Paper](https://arxiv.org/abs/2306.15412).

The inference adapter in `../vocal_pitch.py` uses a 128-bin mel spectrogram,
1024-sample analysis window, local pitch-bin averaging, and the pretrained
checkpoint from [yxlllc/RMVPE release 230917](https://github.com/yxlllc/RMVPE/releases/tag/230917).
These inference settings match that release rather than the original training
configuration. Audio framing, bounded chunks, downloads, cache management, and
CREPE cross-checking are implemented by AudioTransfer.

No model weights or training data are distributed with AudioTransfer.
