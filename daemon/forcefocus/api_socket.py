import os
import json
import time
import socket
import logging
from pathlib import Path
from forcefocus.constants import SOCK_PATH, SOCKET_TIMEOUT

class SocketAPIManager:
    def __init__(self, daemon):
        self.daemon = daemon

    def socket_server(self):
        if os.path.exists(SOCK_PATH):
            os.unlink(SOCK_PATH)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(SOCK_PATH)
        os.chmod(SOCK_PATH, 0o600)

        user_file = Path("/etc/forcefocus/user")
        if user_file.exists():
            try:
                import pwd
                username = user_file.read_text().strip()
                uid = pwd.getpwnam(username).pw_uid
                os.chown(SOCK_PATH, uid, -1)
            except Exception as exc:
                logging.error("Failed to chown socket: %s", exc)

        sock.listen(5)
        sock.settimeout(SOCKET_TIMEOUT)
        logging.info("Command socket listening at %s.", SOCK_PATH)

        try:
            while not self.daemon.shutdown_event.is_set():
                try:
                    conn, _ = sock.accept()
                except socket.timeout:
                    continue
                except OSError as exc:
                    if self.daemon.shutdown_event.is_set():
                        break
                    logging.error("Socket accept error: %s", exc)
                    time.sleep(1)
                    continue
                try:
                    conn.settimeout(5.0)
                    MAX_MSG_SIZE = 1 * 1024 * 1024  # 1MB
                    chunks = []
                    total_size = 0
                    while True:
                        chunk = conn.recv(8192)
                        if not chunk:
                            break
                        total_size += len(chunk)
                        if total_size > MAX_MSG_SIZE:
                            logging.warning("Socket message exceeded %d bytes.", MAX_MSG_SIZE)
                            conn.sendall(json.dumps({"status": "error", "error_code": "INVALID_INPUT", "message": "Message too large."}).encode("utf-8"))
                            chunks = []
                            break
                        chunks.append(chunk)
                    raw = b"".join(chunks).decode("utf-8").strip()
                    if not raw:
                        continue
                    response = self.dispatch_command(raw)
                    conn.sendall(json.dumps(response).encode("utf-8"))
                except Exception as exc:
                    logging.error("Socket handler error: %s", exc)
                    try:
                        conn.sendall(json.dumps({"status": "error", "error_code": "SYSTEM_FAILURE", "message": "The command could not be completed."}).encode("utf-8"))
                    except Exception:
                        pass
                finally:
                    conn.close()
        finally:
            sock.close()
            try:
                os.unlink(SOCK_PATH)
            except FileNotFoundError:
                pass

    def dispatch_command(self, raw: str) -> dict:
        try:
            cmd = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {
                "status": "error",
                "error_code": "INVALID_INPUT",
                "message": "Malformed JSON.",
            }
        return self.daemon.command_service.dispatch(cmd)
