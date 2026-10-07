import os

# Tests must never depend on a real LLM key.
os.environ.pop("OPENAI_API_KEY", None)
