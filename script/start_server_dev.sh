#/bin/bash

find . -name "*.py" | entr -r sh -c \
'uv run python -X jit src/main.py --host 0.0.0.0 --config config/example.hcl'
