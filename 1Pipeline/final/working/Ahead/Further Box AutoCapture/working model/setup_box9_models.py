"""Explicit one-time provisioning. Never imported by runtime."""
from pathlib import Path
import json
import os
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parent
MODELS = {"table_detection": "microsoft/table-transformer-detection",
          "table_structure": "microsoft/table-transformer-structure-recognition",
          "qa": "deepset/deberta-v3-base-squad2"}

if __name__ == '__main__':
    for name, repo in MODELS.items():
        print('Provisioning', repo, flush=True)
        # Table models are unchanged from Box8 and can be reused. Only the QA
        # checkpoint is new for Box9.
        destination=str(ROOT / ('box9_models' if name == 'qa' else 'box8_models') / name)
        if os.name=='nt':destination='\\\\?\\'+destination
        snapshot_download(repo, local_dir=destination,
            allow_patterns=['config.json', 'preprocessor_config.json', 'tokenizer*',
                'vocab.json', 'vocab.txt', 'merges.txt', 'special_tokens_map.json',
                'sentencepiece.*', 'spiece.model', 'model.safetensors',
                'pytorch_model.bin'], max_workers=3)
    (ROOT / 'box9_models' / 'sources.json').write_text(json.dumps(MODELS, indent=2))
    print('BOX9_MODELS_READY', flush=True)
