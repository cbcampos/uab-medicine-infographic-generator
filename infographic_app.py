"""
UAB Medicine Infographic Generator
GPT Image 2.0 (OpenAI direct or Azure OpenAI) · notex-style prompt architecture

Implementation is split under `uab_app/` for maintainability; this file remains the
Streamlit entrypoint (`streamlit run infographic_app.py`).
"""

from uab_app.ui import main

if __name__ == "__main__":
    main()
