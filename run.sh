#!/bin/bash
lsof -ti :8080 | xargs kill -9 2>/dev/null
cd "$(dirname "$0")/backend" && uvicorn main:app --reload --port 8080
