#!/usr/bin/env bash
# Active Video Perception: parallel evaluation.
# Set ANNOTATION_FILE, OUTPUT_DIR, and CONFIG_FILE before running.
# For re-running error samples set ANNOTATION_FILE to your error-samples JSON and OUTPUT_DIR as desired.

ANNOTATION_FILE=./avp/eval_anno/eval_lvbench.json
OUTPUT_DIR=./avp/out
CONFIG_FILE=./avp/config.example.json
API_KEY=""
export GEMINI_API_KEY=$API_KEY

LIMIT=10
MAX_TURNS=3
NUM_WORKERS=2
TIMEOUT=2000

python -m avp.eval_parallel \
    --ann $ANNOTATION_FILE \
    --out $OUTPUT_DIR \
    --config $CONFIG_FILE \
    --max-turns $MAX_TURNS \
    --num-workers $NUM_WORKERS \
    --limit $LIMIT \
    --timeout $TIMEOUT
