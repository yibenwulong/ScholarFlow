"""Start a real API and a separate worker using explicit synthetic fixtures."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description='ScholarFlow synthetic demo; no external model or search calls')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--data-dir', default='data/scholarflow-demo')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, SCHOLARFLOW_DEMO='true', SCHOLARFLOW_DATA_DIR=str(Path(args.data_dir).resolve()), PYTHONUNBUFFERED='1')
    commands = [[sys.executable, '-m', 'uvicorn', 'backend.scholarflow.api.app:create_app', '--factory', '--host', '127.0.0.1', '--port', str(args.port)],
                [sys.executable, '-m', 'backend.scholarflow.worker']]
    children = []
    def stop(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    print('ScholarFlow DEMO: self-authored synthetic papers, model responses and PDF files. No real scientific results.', flush=True)
    try:
        for command in commands:
            children.append(subprocess.Popen(command, cwd=root, env=environment))
        while all(child.poll() is None for child in children):
            time.sleep(0.25)
        return next((child.returncode for child in children if child.returncode), 1)
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
