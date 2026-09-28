"""Explicit one-time provisioning. Never imported by runtime."""
from pathlib import Path
import json
import os
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parent
MODELS = {"table_detection": "microsoft/table-transformer-detection",
          "table_structure": "microsoft/table-transformer-structure-recognition",
          "qa": "deepset/roberta-base-squad2"}

if __name__ == '__main__':
    for name, repo in MODELS.items():
        print('Provisioning', repo, flush=True)
        destination=str(ROOT / 'box8_models' / name)
        if os.name=='nt':destination='\\\\?\\'+destination
        snapshot_download(repo, local_dir=destination,
            allow_patterns=['config.json', 'preprocessor_config.json', 'tokenizer*.json',
                'vocab.json', 'merges.txt', 'special_tokens_map.json', 'model.safetensors',
                'pytorch_model.bin'], max_workers=3)
    (ROOT / 'box8_models' / 'sources.json').write_text(json.dumps(MODELS, indent=2))
    print('BOX8_MODELS_READY', flush=True)
