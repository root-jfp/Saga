"""
Saga launcher.

Runs the Saga server in a background thread, opens the browser, and shows a
small Tk window with the local and LAN URLs so users can also reach the app
from a phone or tablet on the same network.

Usage:
    pythonw launch.py        # Windows — no console window (preferred)
    python  launch.py        # Any platform — works fine, leaves a console open
    Saga.bat                 # Windows double-click shortcut (calls pythonw)

Closing the Tk window stops the server.
"""

import os
import socket
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from tkinter import ttk

PORT = 5001
HOST_BIND = '0.0.0.0'  # so the LAN URL is reachable from other devices


def _redirect_stdio_to_devnull_if_detached():
    """Under pythonw.exe, sys.stdout/stderr may be None; print() then crashes.
    Redirect to devnull so the server thread's logging doesn't blow up."""
    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, 'w')
        if sys.stderr is None:
            sys.stderr = open(os.devnull, 'w')
    except Exception:
        pass


def _local_lan_ip():
    """Best-effort detection of a non-loopback LAN IP. Returns None if unreachable."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        # Connecting a UDP socket doesn't actually send anything; it just lets
        # the kernel pick the route, so getsockname() reveals the local IP.
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith('127.'):
            return ip
    except Exception:
        pass
    return None


def _is_health_ok(port, timeout=1.0):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _wait_until_up(port, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_health_ok(port):
            return True
        time.sleep(0.3)
    return False


_server_error = [None]  # mutable holder so the thread can report failures


def _start_server(port):
    try:
        from app import app, init_db, _start_audio_worker_thread
        init_db()
        _start_audio_worker_thread()
        from waitress import serve
        serve(app, host=HOST_BIND, port=port, threads=8)
    except Exception as e:
        _server_error[0] = repr(e)


def _build_window(local_url, lan_url, status_text, status_ok, server_error):
    root = tk.Tk()
    root.title('Saga')
    root.resizable(False, False)

    pad = 14
    frame = ttk.Frame(root, padding=pad)
    frame.grid(row=0, column=0, sticky='nsew')

    status_color = '#2a7a3a' if status_ok else '#a83232'
    tk.Label(
        frame, text=status_text, fg=status_color,
        font=('Segoe UI', 13, 'bold')
    ).grid(row=0, column=0, columnspan=2, sticky='w')

    if server_error:
        tk.Label(
            frame, text=server_error, fg='#a83232',
            wraplength=440, justify='left'
        ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(6, 0))

    row = 2
    if status_ok:
        tk.Label(frame, text='Open in your browser:').grid(
            row=row, column=0, columnspan=2, sticky='w', pady=(pad, 2)
        )
        row += 1
        local_entry = ttk.Entry(frame, width=46, font=('Consolas', 10))
        local_entry.insert(0, local_url)
        local_entry.configure(state='readonly')
        local_entry.grid(row=row, column=0, columnspan=2, sticky='ew')
        row += 1

        if lan_url:
            tk.Label(
                frame, text='On this network (phone, tablet, other devices):'
            ).grid(row=row, column=0, columnspan=2, sticky='w', pady=(pad, 2))
            row += 1
            lan_entry = ttk.Entry(frame, width=46, font=('Consolas', 10))
            lan_entry.insert(0, lan_url)
            lan_entry.configure(state='readonly')
            lan_entry.grid(row=row, column=0, columnspan=2, sticky='ew')
            row += 1

        tk.Label(
            frame,
            text='Closing this window stops Saga.',
            fg='#6b6b6b',
        ).grid(row=row, column=0, columnspan=2, sticky='w', pady=(pad, 0))
        row += 1

        btn_row = ttk.Frame(frame)
        btn_row.grid(row=row, column=0, columnspan=2, sticky='ew', pady=(pad, 0))
        ttk.Button(
            btn_row, text='Open in Browser',
            command=lambda: webbrowser.open(local_url),
        ).pack(side='left')
        ttk.Button(
            btn_row, text='Stop Saga', command=root.destroy,
        ).pack(side='right')

    # Surface the window above other apps when first launched, then drop the
    # always-on-top so it doesn't annoy.
    root.after(50, lambda: root.attributes('-topmost', True))
    root.after(700, lambda: root.attributes('-topmost', False))
    return root


def main():
    _redirect_stdio_to_devnull_if_detached()

    already = _is_health_ok(PORT)
    if not already:
        threading.Thread(
            target=_start_server, args=(PORT,),
            daemon=True, name='SagaServer',
        ).start()
        up = _wait_until_up(PORT)
    else:
        up = True

    local_url = f'http://localhost:{PORT}'
    lan_ip = _local_lan_ip()
    lan_url = f'http://{lan_ip}:{PORT}' if lan_ip else None

    # Auto-open the browser on first successful start.
    if up and not already:
        try:
            webbrowser.open(local_url)
        except Exception:
            pass

    if up:
        status = 'Saga is running'
    else:
        status = 'Saga did not start'

    root = _build_window(local_url, lan_url, status, up, _server_error[0])
    root.mainloop()
    # Force exit so the daemon Waitress thread terminates with the UI.
    os._exit(0)


if __name__ == '__main__':
    main()
