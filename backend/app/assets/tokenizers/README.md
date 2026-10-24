# BGE-M3 tokenizer

`bge-m3.json.gz` is the losslessly compressed public `tokenizer.json` from
https://huggingface.co/BAAI/bge-m3/resolve/main/tokenizer.json (retrieved 2026-10-23).
Original JSON SHA-256:
`21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08`.
Upstream model/license: https://huggingface.co/BAAI/bge-m3 .

Official `tokenizer_config.json` declares `model_max_length: 8192` and
`tokenizer_class: XLMRobertaTokenizer`. The backend disables tokenizer truncation
and padding, counts content tokens, and reserves the tokenizer's two special
tokens. No model weights are bundled and no runtime network download is needed.

A different embedding model requires its own `QA_TOKENIZER_PATH` (JSON or gzip)
and `QA_EMBED_MAX_TOKENS`. Configuration changes invalidate the QA revision only.
