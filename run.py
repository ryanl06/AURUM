"""Inicia o AURUM: servidor local em http://localhost:8765.

Uso:
  python run.py                 abre o navegador e mostra o servidor no console
  pythonw run.py --background   roda escondido (sem janela), gravando o log em data/aurum.log

Se o AURUM já estiver rodando, apenas abre o navegador — nunca sobe duas cópias.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from app.config import DATA_DIR, HOST, PORT


def running_instance(port: int) -> dict | None:
    """Resposta do /api/health se já houver um AURUM nesta porta."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data if data.get("app") == "AURUM" else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def reserve_port(host: str, port: int) -> socket.socket | None:
    """Reserva a porta já na largada: se outra cópia do AURUM subir ao mesmo tempo, só uma consegue."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: impede que dois processos dividam a porta
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind((host, port))
    except OSError:
        sock.close()
        return None
    return sock


def wait_for_instance(port: int, seconds: float = 30) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if running_instance(port):
            return True
        time.sleep(0.5)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="AURUM — analisador de mercado")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true", help="não abrir o navegador automaticamente")
    parser.add_argument("--background", action="store_true", help="rodar sem janela, com log em data/aurum.log")
    args = parser.parse_args()
    url = f"http://localhost:{args.port}"

    sock = reserve_port(args.host, args.port)
    if sock is None:
        # Porta ocupada: ou o AURUM já está rodando (talvez ainda iniciando) ou é outro programa.
        if wait_for_instance(args.port):
            if not args.no_browser and not args.background:
                webbrowser.open(url)
            print(f"AURUM já está rodando em {url}")
            return
        print(f"A porta {args.port} está ocupada por outro programa. Feche-o ou use --port 8766.")
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if args.background or sys.stdout is None:  # pythonw não tem console: tudo vai para o arquivo de log
        log = open(DATA_DIR / "aurum.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    (DATA_DIR / "aurum.pid").write_text(str(os.getpid()), encoding="utf-8")

    print(f"\n  AURUM iniciando em {url}  ({time.strftime('%d/%m/%Y %H:%M:%S')}, pid {os.getpid()})\n  Ctrl+C para encerrar.\n", flush=True)
    if not args.no_browser and not args.background:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()

    import uvicorn

    config = uvicorn.Config("app.main:app", host=args.host, port=args.port, log_level="warning", log_config=None)
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
