"""Inicia o LetsSing Playlist Builder em http://localhost:8000.

Parâmetros ficam aqui no topo (sem sys.argv).
"""

import uvicorn

from letssing.api.app import create_app
from letssing.logging_setup import setup_logging

HOST = "127.0.0.1"  # só acessível nesta máquina
PORT = 8000

if __name__ == "__main__":
    setup_logging()
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="info")
