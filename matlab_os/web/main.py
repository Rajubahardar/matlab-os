"""Entry point for FastAPI server serving the Local AI OS web UI.
Run with:
    uvicorn matlab_os.web.main:app --host 0.0.0.0 --port 8000
"""

import uvicorn
from .api import app  # re‑export the FastAPI instance

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
