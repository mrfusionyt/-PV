# Dependencies:
#   & 'C:\Program Files\Python314\python.exe' -m pip install --upgrade Flask Pillow requests send2trash
#
# Build to .exe:
#   & 'C:\Program Files\Python314\python.exe' -m PyInstaller --noconfirm --windowed --name PhotoClassifier --icon=img/8080.png --add-data "img;img" main.py

import os
import sys
import json
import socket
import shutil
import queue
import threading
import webbrowser
import subprocess
import tkinter as tk
import zipfile
import time
import io
from tkinter import filedialog, messagebox
from pathlib import Path
from collections import deque
import requests
import send2trash
from PIL import Image

from flask import Flask, request, jsonify, send_file

# =====================================================================
# UPDATE SETTINGS
# =====================================================================
CURRENT_VERSION = "1.6.0"
GITHUB_USER = "mrfusionyt"
GITHUB_REPO = "-PV"
INSTALL_DIR = r"C:\!PV" 
# =====================================================================

SUPPORTED = ('.jpg', '.jpeg', '.png', '.gif', '.bmp',
             '.webp', '.tiff', '.tif', '.ico', '.ppm', '.pgm')

CONFIG_PATH = Path.home() / '.photo_classifier_config.json'

app = Flask(__name__)

state = {
    'source': None,
    'targets': {'not_nude': None, 'nude': None},
    'labels': {'not_nude': 'Not Nude', 'nude': 'Nude'},
    'images': [],
}

moves = deque(maxlen=10)
moves_lock = threading.Lock()

dialog_req = queue.Queue()
dialog_res = queue.Queue()
tk_root = None
status_label = None
quit_requested = threading.Event()
ignored_version = None

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def load_config():
    if not CONFIG_PATH.exists(): return
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        state['source'] = data.get('source') or None
        state['targets']['not_nude'] = data.get('not_nude') or None
        state['targets']['nude'] = data.get('nude') or None
        state['labels']['not_nude'] = data.get('label_not_nude') or 'Not Nude'
        state['labels']['nude'] = data.get('label_nude') or 'Nude'
    except Exception: pass

def save_config():
    try:
        CONFIG_PATH.write_text(json.dumps({
            'source': state['source'],
            'not_nude': state['targets']['not_nude'],
            'nude': state['targets']['nude'],
            'label_not_nude': state['labels']['not_nude'],
            'label_nude': state['labels']['nude'],
        }, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception: pass

def scan_source():
    src = state['source']
    if not src or not os.path.isdir(src):
        state['images'] = []
        return
    try:
        names = os.listdir(src)
    except OSError:
        state['images'] = []
        return
    import re
    def key(f):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', f)]
    state['images'] = sorted([f for f in names if f.lower().endswith(SUPPORTED)], key=key)

def is_safe_name(name):
    return (bool(name) and '/' not in name and '\\' not in name and '..' not in name and ':' not in name)

def ask_folder(title):
    dialog_req.put(title)
    return dialog_res.get()

def get_local_ips():
    ips = []
    try:
        _, _, addrs = socket.gethostbyname_ex(socket.gethostname())
        for ip in addrs:
            if ip not in ips and not ip.startswith('127.') and ip != '0.0.0.0':
                ips.append(ip)
    except Exception: pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        if ip not in ips and not ip.startswith('127.') and ip != '0.0.0.0':
            ips.append(ip)
    except Exception: pass
    return ips

# ----------------------- Auto-Update Logic -----------------------

def check_for_updates():
    global ignored_version
    if GITHUB_USER == "YOUR_NICKNAME" or GITHUB_REPO == "YOUR_REPO":
        return

    try:
        url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/releases/latest"
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        data = r.json()
        
        latest_version = data.get('tag_name', '').lstrip('v')
        release_url = data.get('html_url', '')
        
        if latest_version and latest_version != CURRENT_VERSION and latest_version != ignored_version:
            download_url = None
            for asset in data.get('assets', []):
                if asset['name'].endswith('.zip'):
                    download_url = asset['browser_download_url']
                    break
            
            if tk_root:
                tk_root.after(0, lambda: show_update_dialog(latest_version, release_url, download_url))
    except Exception as e:
        print(f"Update check error: {e}")

def update_loop():
    while not quit_requested.is_set():
        check_for_updates()
        for _ in range(3600):
            if quit_requested.is_set(): break
            time.sleep(1)

def show_update_dialog(latest_version, release_url, download_url):
    global ignored_version
    msg = (f"A new version is available: {latest_version}\n"
           f"Your version: {CURRENT_VERSION}\n\n"
           f"Download and install automatically?")
    
    if not getattr(sys, 'frozen', False) or not download_url:
        ans = messagebox.askyesno("Update Available!", f"{msg}\n\n(Will open browser)", parent=tk_root)
        if ans: 
            webbrowser.open(release_url)
        else:
            ignored_version = latest_version
        return

    ans = messagebox.askyesno("Update Available!", msg, parent=tk_root)
    if ans:
        if status_label:
            status_label.config(text="Downloading update...", fg="#ff3b5c")
        threading.Thread(target=download_and_update, args=(download_url,), daemon=True).start()
    else:
        ignored_version = latest_version

def download_and_update(download_url):
    try:
        exe_name = "PhotoClassifier.exe"
        zip_name = "update.zip"
        zip_path = os.path.join(os.environ.get('TEMP', '.'), zip_name)
        update_dir = os.path.join(os.environ.get('TEMP', '.'), "pc_update_temp")
        
        r = requests.get(download_url, stream=True)
        r.raise_for_status()
        with open(zip_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                
        if os.path.exists(update_dir):
            shutil.rmtree(update_dir)
        os.makedirs(update_dir)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(update_dir)

        bat_path = os.path.join(os.environ.get('TEMP', '.'), "update_pc.bat")
        with open(bat_path, "w") as f:
            f.write(f"""@echo off
timeout /t 2 /nobreak > NUL
taskkill /IM "{exe_name}" /F > NUL 2>&1
xcopy /s /y /i /q "{update_dir}\\*" "{INSTALL_DIR}\\"
rmdir /s /q "{update_dir}"
del "{zip_path}"
start "" "{INSTALL_DIR}\\{exe_name}"
del "%~f0"
""")

        subprocess.Popen(f'"{bat_path}"', shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
        quit_requested.set()

    except Exception as e:
        if tk_root:
            tk_root.after(0, lambda: messagebox.showerror("Update Error", f"Failed to update: {e}", parent=tk_root))

# ----------------------- routes -----------------------

@app.route('/')
def index(): return HTML_PAGE

@app.route('/favicon.ico')
def favicon(): return send_file(resource_path('img/4040.png'), mimetype='image/png')

@app.route('/api/state')
def api_state():
    with moves_lock: undo_count = len(moves)
    return jsonify({
        'images': state['images'], 
        'settings': {
            'source': state['source'] or '', 
            'not_nude': state['targets']['not_nude'] or '', 
            'nude': state['targets']['nude'] or '',
            'label_not_nude': state['labels']['not_nude'],
            'label_nude': state['labels']['nude']
        }, 
        'undo_count': undo_count
    })

@app.route('/api/restart', methods=['POST'])
def api_restart():
    load_config()
    scan_source()
    with moves_lock: undo_count = len(moves)
    return jsonify({
        'ok': True, 
        'images': state['images'], 
        'settings': {
            'source': state['source'] or '', 
            'not_nude': state['targets']['not_nude'] or '', 
            'nude': state['targets']['nude'] or '',
            'label_not_nude': state['labels']['not_nude'],
            'label_nude': state['labels']['nude']
        }, 
        'undo_count': undo_count
    })

@app.route('/api/pick-folder', methods=['POST'])
def api_pick_folder():
    data = request.get_json() or {}
    title = data.get('title') or 'Select folder'
    try: path = ask_folder(title)
    except Exception as e: return jsonify({'path': '', 'error': str(e)})
    return jsonify({'path': path or ''})

@app.route('/api/save-settings', methods=['POST'])
def api_save_settings():
    data = request.get_json() or {}
    source = (data.get('source') or '').strip() or None
    not_nude = (data.get('not_nude') or '').strip() or None
    nude = (data.get('nude') or '').strip() or None
    
    label_nn = (data.get('label_not_nude') or '').strip() or 'Not Nude'
    label_n = (data.get('label_nude') or '').strip() or 'Nude'

    if source and not os.path.isdir(source): return jsonify({'ok': False, 'error': 'Source folder does not exist'})
    if not_nude and not os.path.isdir(not_nude): return jsonify({'ok': False, 'error': 'Folder "Not Nude" does not exist'})
    if nude and not os.path.isdir(nude): return jsonify({'ok': False, 'error': 'Folder "Nude" does not exist'})
    
    state['source'] = source
    state['targets']['not_nude'] = not_nude
    state['targets']['nude'] = nude
    state['labels']['not_nude'] = label_nn
    state['labels']['nude'] = label_n
    
    save_config()
    scan_source()
    with moves_lock: undo_count = len(moves)
    return jsonify({'ok': True, 'images': state['images'], 'undo_count': undo_count})

@app.route('/api/image')
def api_image():
    name = request.args.get('name', '')
    thumb = request.args.get('thumb', '0') == '1'
    
    if not is_safe_name(name) or not state['source']: return 'bad', 400
    path = os.path.join(state['source'], name)
    if not os.path.isfile(path): return 'not found', 404
    
    if thumb:
        try:
            # На лету уменьшаем фото для быстрой загрузки в браузере
            img = Image.open(path)
            img.thumbnail((1920, 1080), Image.LANCZOS)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=85)
            buf.seek(0)
            return send_file(buf, mimetype='image/jpeg')
        except Exception:
            pass # Если не удалось сжать (например, битый файл), отдаем оригинал
            
    return send_file(path)

@app.route('/api/classify', methods=['POST'])
def api_classify():
    data = request.get_json() or {}
    name = data.get('name', '')
    category = data.get('category', '')
    if category not in state['targets']: return jsonify({'ok': False, 'error': 'Unknown category'})
    target = state['targets'][category]
    if not target: return jsonify({'ok': False, 'error': 'Folder not selected in settings'})
    if not os.path.isdir(target): return jsonify({'ok': False, 'error': 'Folder does not exist'})
    if not is_safe_name(name) or not state['source']: return jsonify({'ok': False, 'error': 'Invalid file name'})
    src = os.path.join(state['source'], name)
    if not os.path.isfile(src): return jsonify({'ok': False, 'error': 'File not found'})

    dst = os.path.join(target, name)
    base, ext = os.path.splitext(name)
    i = 1
    while os.path.exists(dst):
        dst = os.path.join(target, f'{base}_{i}{ext}')
        i += 1
    try: shutil.move(src, dst)
    except Exception as e: return jsonify({'ok': False, 'error': str(e)})

    with moves_lock:
        moves.append({'src': src, 'dst': dst, 'name': name})
        undo_count = len(moves)
    scan_source()
    return jsonify({'ok': True, 'images': state['images'], 'undo_count': undo_count})

@app.route('/api/undo', methods=['POST'])
def api_undo():
    with moves_lock:
        if not moves: return jsonify({'ok': False, 'error': 'No operations to undo'})
        m = moves.pop()
    src = m['src']; dst = m['dst']

    if not os.path.isfile(dst):
        with moves_lock: undo_count = len(moves)
        return jsonify({'ok': False, 'error': 'File already moved or deleted', 'undo_count': undo_count})
    src_dir = os.path.dirname(src)
    if not os.path.isdir(src_dir):
        with moves_lock: undo_count = len(moves)
        return jsonify({'ok': False, 'error': 'Source folder no longer exists', 'undo_count': undo_count})

    target_path = src
    if os.path.exists(target_path):
        base, ext = os.path.splitext(src)
        i = 1
        while os.path.exists(f'{base}_undo{i}{ext}'): i += 1
        target_path = f'{base}_undo{i}{ext}'

    try: shutil.move(dst, target_path)
    except Exception as e:
        with moves_lock: moves.append(m)
        return jsonify({'ok': False, 'error': str(e)})

    scan_source()
    with moves_lock: undo_count = len(moves)
    return jsonify({'ok': True, 'images': state['images'], 'undo_count': undo_count})

@app.route('/api/delete', methods=['POST'])
def api_delete():
    data = request.get_json() or {}
    name = data.get('name', '')
    if not is_safe_name(name) or not state['source']: return jsonify({'ok': False, 'error': 'Invalid file name'})
    path = os.path.join(state['source'], name)
    if not os.path.isfile(path): return jsonify({'ok': False, 'error': 'File not found'})
    try: 
        send2trash.send2trash(path) # Отправляем в Корзину!
    except Exception as e: 
        return jsonify({'ok': False, 'error': str(e)})
    scan_source()
    with moves_lock: undo_count = len(moves)
    return jsonify({'ok': True, 'images': state['images'], 'undo_count': undo_count})

@app.route('/api/quit', methods=['POST'])
def api_quit():
    quit_requested.set()
    return jsonify({'ok': True})

# ----------------------- Tkinter window & main -----------------------

def tk_pump():
    if quit_requested.is_set():
        try: tk_root.destroy()
        except Exception: pass
        return
    try: title = dialog_req.get_nowait()
    except queue.Empty:
        tk_root.after(100, tk_pump)
        return
    try: path = filedialog.askdirectory(title=title, parent=tk_root) or ''
    except Exception: path = ''
    dialog_res.put(path)
    tk_root.after(100, tk_pump)

def find_free_port(start=5123, end=5223):
    for port in range(start, end):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('0.0.0.0', port))
            return port
        except OSError: continue
        finally: s.close()
    raise RuntimeError('Could not find a free port')

def create_dark_button(parent, text, command, is_primary=False):
    bg = '#ff3b5c' if is_primary else '#2a2a2a'
    fg = '#ffffff'
    active_bg = '#e63946' if is_primary else '#3a3a3a'
    btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=active_bg, activeforeground=fg, font=('Segoe UI', 10, 'bold' if is_primary else 'normal'), relief='flat', bd=0, padx=20, pady=8, cursor='hand2')
    if not is_primary:
        btn.bind('<Enter>', lambda e: btn.config(bg='#3a3a3a'))
        btn.bind('<Leave>', lambda e: btn.config(bg='#2a2a2a'))
    else:
        btn.bind('<Enter>', lambda e: btn.config(bg='#e63946'))
        btn.bind('<Leave>', lambda e: btn.config(bg='#ff3b5c'))
    return btn

def main():
    global tk_root, status_label

    load_config()
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if os.path.isfile(arg): state['source'] = os.path.dirname(os.path.abspath(arg)); save_config()
        elif os.path.isdir(arg): state['source'] = os.path.abspath(arg); save_config()

    scan_source()
    port = find_free_port()
    local_url = f'http://127.0.0.1:{port}'
    lan_ips = get_local_ips()

    tk_root = tk.Tk()
    tk_root.title('PhotoClassifier')
    tk_root.geometry('380x380')
    tk_root.resizable(False, False)
    tk_root.configure(bg='#141418')

    try:
        icon_img = tk.PhotoImage(file=resource_path('img/8080.png'))
        tk_root.iconphoto(True, icon_img)
    except Exception: pass

    status_label = tk.Label(tk_root, text='Application is running.', bg='#141418', fg='#ffffff', font=('Segoe UI', 12, 'bold'))
    status_label.pack(pady=(20, 10))
    
    tk.Label(tk_root, text=f'Version {CURRENT_VERSION}', bg='#141418', fg='#666666', font=('Segoe UI', 9)).pack()
    tk.Label(tk_root, text='On this PC:', bg='#141418', fg='#888888', font=('Segoe UI', 9)).pack(pady=(10, 0))
    tk.Label(tk_root, text=local_url, bg='#141418', fg='#ff3b5c', font=('Consolas', 12, 'bold')).pack(pady=(2, 15))

    if lan_ips:
        tk.Label(tk_root, text='From local network / VPN:', bg='#141418', fg='#888888', font=('Segoe UI', 9)).pack()
        urls_frame = tk.Frame(tk_root, bg='#1e1e24', padx=15, pady=10)
        urls_frame.pack(pady=(5, 15), fill='x', padx=40)
        for ip in lan_ips:
            tk.Label(urls_frame, text=f'http://{ip}:{port}', bg='#1e1e24', fg='#ff3b5c', font=('Consolas', 11)).pack(anchor='w')

    create_dark_button(tk_root, 'Open in browser', lambda: webbrowser.open(local_url), is_primary=True).pack(pady=(10, 8))
    create_dark_button(tk_root, 'Exit', lambda: quit_requested.set()).pack(pady=(0, 20))

    tk_root.protocol('WM_DELETE_WINDOW', lambda: quit_requested.set())

    def run_flask():
        try: app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)
        except Exception as e: print('Flask error:', e)

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()

    tk_root.after(100, tk_pump)
    tk_root.mainloop()

HTML_PAGE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>PhotoClassifier</title>
<link rel="icon" type="image/png" href="/favicon.ico">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; overflow: hidden; -webkit-text-size-adjust: 100%; -webkit-tap-highlight-color: transparent; }
  body { background: #111; color: #eee; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; display: flex; flex-direction: column; height: 100vh; height: 100dvh; }
  
  #progress-container { position: absolute; top: 0; left: 0; width: 100%; height: 3px; background: #333; z-index: 10; }
  #progress-bar { height: 100%; background: #2d7d46; width: 0%; transition: width 0.3s ease; }

  header { display: flex; align-items: center; gap: 8px; padding: 8px 12px; background: #1a1a1a; border-bottom: 1px solid #333; flex-wrap: wrap; position: relative; z-index: 5; }
  header .title { font-weight: 600; font-size: 14px; }
  header .spacer { flex: 1; }
  header button { background: #2a2a2a; color: #eee; border: 1px solid #444; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; touch-action: manipulation; white-space: nowrap; }
  header button:hover { background: #3a3a3a; }
  header button:disabled { opacity: 0.35; cursor: not-allowed; }
  
  #main { flex: 1; display: flex; align-items: center; justify-content: center; position: relative; overflow: hidden; padding: 6px; touch-action: none; min-height: 0; }
  #img-container { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; overflow: hidden; }
  #photo { max-width: 100%; max-height: 100%; object-fit: contain; display: none; user-select: none; -webkit-user-drag: none; transition: filter 0.1s linear, transform 0.1s ease-out; will-change: filter, transform; transform-origin: center center; }
  #photo-placeholder { color: #666; font-size: 16px; text-align: center; padding: 30px; }
  
  .nav { position: absolute; top: 50%; transform: translateY(-50%); background: rgba(0,0,0,0.35); color: #fff; border: none; width: 70px; height: 130px; font-size: 40px; cursor: pointer; opacity: 0.65; transition: opacity 0.15s, background 0.15s; user-select: none; touch-action: manipulation; display: flex; align-items: center; justify-content: center; z-index: 10; }
  .nav:hover { opacity: 1; background: rgba(0,0,0,0.6); }
  .nav:active { background: rgba(0,0,0,0.8); }
  #prev { left: 0; border-radius: 0 10px 10px 0; }
  #next { right: 0; border-radius: 10px 0 0 10px; }
  
  #toolbar { display: flex; align-items: center; gap: 10px; padding: 8px 14px; background: #1a1a1a; border-top: 1px solid #333; justify-content: center; flex-wrap: wrap; z-index: 5; position: relative; }
  #toolbar button { background: #2a2a2a; color: #eee; border: 1px solid #444; width: 48px; height: 48px; border-radius: 8px; cursor: pointer; font-size: 22px; font-weight: 600; touch-action: manipulation; display: flex; align-items: center; justify-content: center; }
  #toolbar button:hover { background: #3a3a3a; }
  #toolbar button:active { background: #4a4a4a; }
  #brightness-value { font-family: Consolas, monospace; color: #ccc; font-size: 16px; min-width: 60px; text-align: center; }
  
  /* НОВОЕ: Поле для прыжка к фото */
  #jump-input { width: 60px; height: 38px; background: #111; color: #fff; border: 1px solid #555; border-radius: 6px; text-align: center; font-family: Consolas, monospace; font-size: 14px; margin-left: 10px; }
  #jump-input:focus { outline: none; border-color: #ff3b5c; }

  #counter { margin-left: auto; font-size: 12px; color: #888; font-family: Consolas, monospace; padding-left: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 45vw; }
  
  footer { display: flex; gap: 20px; padding: 16px 24px; justify-content: center; background: #1a1a1a; border-top: 1px solid #333; flex-wrap: wrap; z-index: 5; position: relative; }
  .action-btn { padding: 42px 96px; font-size: 48px; font-weight: 700; border: none; border-radius: 14px; cursor: pointer; min-width: 280px; letter-spacing: 1px; transition: transform 0.1s, filter 0.15s; touch-action: manipulation; box-shadow: 0 4px 12px rgba(0,0,0,0.4); word-break: break-word; }
  .action-btn:active { transform: scale(0.97); }
  .action-btn:disabled { opacity: 0.35; cursor: not-allowed; }
  #btn-not-nude { background: #a83a3a; color: #fff; }
  #btn-not-nude:hover:not(:disabled) { filter: brightness(1.15); }
  #btn-nude { background: #2d7d46; color: #fff; }
  #btn-nude:hover:not(:disabled) { filter: brightness(1.15); }
  
  .modal-bg { position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: none; align-items: center; justify-content: center; z-index: 100; padding: 16px; }
  .modal-bg.open { display: flex; }
  .modal { background: #1e1e1e; border: 1px solid #444; border-radius: 12px; padding: 22px; width: 640px; max-width: 100%; max-height: 90vh; overflow-y: auto; }
  .modal h2 { margin-bottom: 16px; font-size: 18px; }
  .modal .field { margin-bottom: 12px; }
  .modal label { display: block; margin-bottom: 6px; font-size: 13px; color: #aaa; }
  .modal .row { display: flex; gap: 8px; }
  .modal input { flex: 1; min-width: 0; padding: 8px 10px; background: #111; border: 1px solid #444; border-radius: 6px; color: #eee; font-size: 13px; font-family: Consolas, monospace; }
  .modal button { padding: 8px 14px; background: #2a2a2a; color: #eee; border: 1px solid #444; border-radius: 6px; cursor: pointer; font-size: 13px; white-space: nowrap; touch-action: manipulation; }
  .modal button:hover { background: #3a3a3a; }
  .modal .actions { display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-top: 18px; }
  .modal .primary { background: #2d7d46; border-color: #2d7d46; color: #fff; }
  .modal .primary:hover { background: #369b56; }
  
  #toast { position: fixed; top: 60px; left: 50%; transform: translateX(-50%); background: rgba(40,40,40,0.96); border: 1px solid #555; color: #eee; padding: 10px 20px; border-radius: 8px; font-size: 14px; opacity: 0; transition: opacity 0.2s; pointer-events: none; z-index: 200; max-width: 80vw; text-align: center; }
  #toast.show { opacity: 1; }
  #toast.error { border-color: #a83a3a; }
  
  @media (max-width: 900px) {
    header { padding: 6px 8px; gap: 6px; }
    header .title { font-size: 13px; }
    header button { padding: 5px 10px; font-size: 12px; }
    #toolbar { padding: 6px 10px; gap: 8px; }
    #toolbar button { width: 42px; height: 42px; font-size: 20px; }
    #counter { max-width: 30vw; }
    #jump-input { width: 50px; height: 34px; font-size: 12px; }
    .nav { width: 56px; height: 110px; font-size: 34px; opacity: 0.75; }
    footer { flex-direction: column; gap: 10px; padding: 12px; }
    .action-btn { width: 100%; min-width: 0; padding: 28px 16px; font-size: 38px; border-radius: 12px; }
    .modal .actions { flex-wrap: wrap; }
    .modal .actions button { flex: 1 1 auto; }
    .modal .actions button:first-child { flex-basis: 100%; margin-right: 0 !important; }
  }
  @media (max-width: 420px) {
    .action-btn { font-size: 32px; padding: 24px 12px; }
    .nav { width: 46px; height: 90px; font-size: 28px; }
    #toolbar button { width: 38px; height: 38px; font-size: 18px; }
  }
</style>
</head>
<body>
  <div id="progress-container"><div id="progress-bar"></div></div>
  
  <header>
    <span class="title">PhotoClassifier</span>
    <span class="spacer"></span>
    <button id="btn-fullscreen" title="Toggle Fullscreen (F)">⛶</button>
    <button id="btn-back" title="Back (←)">◀</button>
    <button id="btn-undo" disabled title="Undo last move (Ctrl+Z)">↶ (0)</button>
    <button id="btn-settings" title="Settings">⚙</button>
    <button id="btn-quit" title="Exit">✕</button>
  </header>
  <div id="main">
    <button id="prev" class="nav" title="Back (←)">‹</button>
    <button id="next" class="nav" title="Next (→)">›</button>
    <div id="img-container">
      <img id="photo" alt="" draggable="false">
      <div id="photo-placeholder">Click ⚙ to select a photo folder</div>
    </div>
  </div>
  <div id="toolbar">
    <button id="b-down" title="Darker (↓)">☀−</button>
    <span id="brightness-value">1.00</span>
    <button id="b-up" title="Brighter (↑)">☀+</button>
    <button id="b-reset" title="Reset brightness (R)">↺</button>
    
    <!-- НОВОЕ: Прыжок к фото -->
    <input type="number" id="jump-input" min="1" placeholder="#">
    
    <span id="counter"></span>
  </div>
  <footer>
    <button class="action-btn" id="btn-not-nude" disabled>Not Nude</button>
    <button class="action-btn" id="btn-nude" disabled>Nude</button>
  </footer>
  <div class="modal-bg" id="settings-modal">
    <div class="modal">
      <h2>Folder Settings</h2>
      
      <div class="field">
        <label>Button 1 Name (Red)</label>
        <div class="row">
          <input type="text" id="label-not-nude" placeholder="Not Nude">
        </div>
      </div>
      <div class="field">
        <label>Button 2 Name (Green)</label>
        <div class="row">
          <input type="text" id="label-nude" placeholder="Nude">
        </div>
      </div>

      <div class="field">
        <label>Photo folder (source)</label>
        <div class="row">
          <input type="text" id="source-folder" readonly placeholder="Not selected">
          <button onclick="pickFolder('source')">Browse...</button>
        </div>
      </div>
      <div class="field">
        <label>Save Button 1 photos to</label>
        <div class="row">
          <input type="text" id="not-nude-folder" readonly placeholder="Not selected">
          <button onclick="pickFolder('not_nude')">Browse...</button>
        </div>
      </div>
      <div class="field">
        <label>Save Button 2 photos to</label>
        <div class="row">
          <input type="text" id="nude-folder" readonly placeholder="Not selected">
          <button onclick="pickFolder('nude')">Browse...</button>
        </div>
      </div>
      <div class="actions">
        <button onclick="restartServer()" style="margin-right:auto;" title="Reload settings and rescan source folder">🔄 Restart server</button>
        <button onclick="closeSettings()">Cancel</button>
        <button class="primary" onclick="saveSettings()">Save</button>
      </div>
    </div>
  </div>
  <div id="toast"></div>
<script>
let images = [];
let currentIndex = 0;
let settings = { source: '', not_nude: '', nude: '', label_not_nude: 'Not Nude', label_nude: 'Nude' };
let brightness = 1.0;

// Переменные для зума
let scale = 1.0;
let translateX = 0, translateY = 0;
let isDragging = false;
let startX, startY;

const $ = (id) => document.getElementById(id);

async function loadState() {
  const r = await fetch('/api/state');
  const data = await r.json();
  images = data.images || [];
  
  // Восстанавливаем последнюю позицию
  const savedIndex = localStorage.getItem('lastPhotoIndex');
  if (savedIndex !== null && parseInt(savedIndex) < images.length) {
    currentIndex = parseInt(savedIndex);
  } else {
    currentIndex = 0;
  }

  settings = data.settings || settings;
  
  $('source-folder').value = settings.source || '';
  $('not-nude-folder').value = settings.not_nude || '';
  $('nude-folder').value = settings.nude || '';
  
  $('label-not-nude').value = settings.label_not_nude || 'Not Nude';
  $('label-nude').value = settings.label_nude || 'Nude';
  
  updateUI();
  updateUndoButton(data.undo_count || 0);
  render();
}

function updateUI() {
  $('btn-not-nude').textContent = settings.label_not_nude || 'Not Nude';
  $('btn-nude').textContent = settings.label_nude || 'Nude';
}

function updateUndoButton(count) {
  const btn = $('btn-undo');
  btn.textContent = '↶ (' + count + ')';
  btn.disabled = count === 0;
  btn.title = count > 0 ? 'Undo last move (' + count + ' available)' : 'Nothing to undo';
}

function render() {
  const img = $('photo'); const ph = $('photo-placeholder'); const counter = $('counter'); const btnNN = $('btn-not-nude'); const btnN = $('btn-nude');
  const progressBar = $('progress-bar');
  
  // Сброс зума при смене фото
  scale = 1.0; translateX = 0; translateY = 0;
  applyTransform();

  if (images.length === 0) {
    img.style.display = 'none'; img.removeAttribute('src'); ph.style.display = 'block';
    ph.textContent = settings.source ? 'No images in the selected folder (or all processed)' : 'Click ⚙ to select a photo folder';
    counter.textContent = ''; btnNN.disabled = true; btnN.disabled = true; applyBrightness(); 
    progressBar.style.width = '0%';
    return;
  }
  if (currentIndex < 0) currentIndex = images.length - 1;
  if (currentIndex >= images.length) currentIndex = images.length - 1;
  
  const name = images[currentIndex];
  
  // Загружаем через новый эндпоинт с параметром thumb=1 (сжатие)
  img.src = '/api/image?name=' + encodeURIComponent(name) + '&thumb=1&t=' + Date.now();
  
  img.style.display = 'block'; ph.style.display = 'none';
  counter.textContent = (currentIndex + 1) + ' / ' + images.length + ' — ' + name; counter.title = name;
  btnNN.disabled = false; btnN.disabled = false; applyBrightness();
  
  // Обновляем полосу прогресса
  const progressPercent = ((currentIndex + 1) / images.length) * 100;
  progressBar.style.width = progressPercent + '%';

  // Сохраняем позицию в localStorage
  localStorage.setItem('lastPhotoIndex', currentIndex);

  // ПРЕДЗАГРУЗКА следующего фото
  if (images.length > 1) {
      const nextIndex = (currentIndex + 1) % images.length;
      const nextName = images[nextIndex];
      const preloadImg = new Image();
      preloadImg.src = '/api/image?name=' + encodeURIComponent(nextName) + '&thumb=1';
  }
}

function applyBrightness() {
  const img = $('photo'); if (!img) return;
  img.style.filter = (brightness === 1.0) ? '' : 'brightness(' + brightness.toFixed(2) + ')';
  $('brightness-value').textContent = brightness.toFixed(2);
}

function changeBrightness(delta) {
  if (images.length === 0) return;
  brightness = Math.max(0.1, Math.min(3.0, brightness + delta));
  applyBrightness();
}

function resetBrightness() { brightness = 1.0; applyBrightness(); }
function nextImage() { if (images.length === 0) return; currentIndex = (currentIndex + 1) % images.length; brightness = 1.0; render(); }
function prevImage() { if (images.length === 0) return; currentIndex = (currentIndex - 1 + images.length) % images.length; brightness = 1.0; render(); }

function jumpToImage() {
    const val = parseInt($('jump-input').value);
    if (!isNaN(val) && val > 0 && val <= images.length) {
        currentIndex = val - 1;
        brightness = 1.0;
        render();
    }
    $('jump-input').value = '';
    $('jump-input').blur();
}

// Функция для зума
function applyTransform() {
    const img = $('photo');
    if (img) {
        img.style.transform = `translate(${translateX}px, ${translateY}px) scale(${scale})`;
    }
}

// Слушатель колесика мыши для зума и навигации
$('img-container').addEventListener('wheel', (e) => {
    if (images.length === 0) return;
    if (e.ctrlKey) return;

    if (scale > 1.0) {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.2 : 0.2;
        scale = Math.max(1.0, Math.min(5.0, scale + delta));
        if (scale === 1.0) { translateX = 0; translateY = 0; }
        applyTransform();
    } else {
        if (e.deltaY > 0) nextImage();
        else prevImage();
    }
}, { passive: false });

// Перетаскивание (Pan)
$('img-container').addEventListener('mousedown', (e) => {
    if (scale > 1.0) {
        isDragging = true;
        startX = e.clientX - translateX;
        startY = e.clientY - translateY;
        $('photo').style.cursor = 'grabbing';
    }
});
window.addEventListener('mousemove', (e) => {
    if (isDragging) {
        translateX = e.clientX - startX;
        translateY = e.clientY - startY;
        applyTransform();
    }
});
window.addEventListener('mouseup', () => {
    isDragging = false;
    $('photo').style.cursor = 'default';
});

// Pinch to zoom
let initialDistance = 0;
$('img-container').addEventListener('touchstart', (e) => {
    if (e.touches.length === 2) {
        initialDistance = Math.hypot(
            e.touches[0].pageX - e.touches[1].pageX,
            e.touches[0].pageY - e.touches[1].pageY
        );
    }
}, { passive: true });
$('img-container').addEventListener('touchmove', (e) => {
    if (e.touches.length === 2) {
        e.preventDefault();
        const currentDistance = Math.hypot(
            e.touches[0].pageX - e.touches[1].pageX,
            e.touches[0].pageY - e.touches[1].pageY
        );
        if (initialDistance > 0) {
            scale = Math.max(1.0, Math.min(5.0, scale * (currentDistance / initialDistance)));
            applyTransform();
            initialDistance = currentDistance;
        }
    }
}, { passive: false });

async function classify(category) {
  if (images.length === 0) return;
  if (!settings[category]) { toast('Select a folder in settings first', true); return; }
  const name = images[currentIndex];
  const r = await fetch('/api/classify', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ name: name, category: category }) });
  const data = await r.json();
  if (!data.ok) { toast('Error: ' + data.error, true); return; }
  images = data.images;
  if (images.length === 0) currentIndex = 0; else if (currentIndex >= images.length) currentIndex = images.length - 1;
  brightness = 1.0; updateUndoButton(data.undo_count); render();
}

async function undoLast() {
  const r = await fetch('/api/undo', { method: 'POST' });
  const data = await r.json();
  updateUndoButton(data.undo_count || 0);
  if (!data.ok) { toast('Error: ' + data.error, true); return; }
  images = data.images;
  if (currentIndex >= images.length) currentIndex = Math.max(0, images.length - 1);
  brightness = 1.0; render();
}

async function deleteImage() {
  if (images.length === 0) return;
  if (!confirm('Move file to Recycle Bin?\n\n' + images[currentIndex])) return;
  const name = images[currentIndex];
  const r = await fetch('/api/delete', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ name: name }) });
  const data = await r.json();
  if (!data.ok) { toast('Error: ' + data.error, true); return; }
  images = data.images;
  if (currentIndex >= images.length) currentIndex = Math.max(0, images.length - 1);
  brightness = 1.0; updateUndoButton(data.undo_count); render();
}

async function pickFolder(key) {
  const titles = { 'source': 'Select photo folder', 'not_nude': 'Select folder for Button 1', 'nude': 'Select folder for Button 2' };
  toast('Opening folder selection dialog...');
  const r = await fetch('/api/pick-folder', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ title: titles[key] }) });
  const data = await r.json();
  if (data.path) {
    if (key === 'source') $('source-folder').value = data.path;
    else if (key === 'not_nude') $('not-nude-folder').value = data.path;
    else $('nude-folder').value = data.path;
  }
}

async function saveSettings() {
  const body = { 
    source: $('source-folder').value, 
    not_nude: $('not-nude-folder').value, 
    nude: $('nude-folder').value,
    label_not_nude: $('label-not-nude').value.trim() || 'Not Nude',
    label_nude: $('label-nude').value.trim() || 'Nude'
  };
  
  const r = await fetch('/api/save-settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
  const data = await r.json();
  if (!data.ok) { toast('Error: ' + data.error, true); return; }
  
  settings = body; images = data.images; currentIndex = 0; brightness = 1.0;
  updateUI();
  updateUndoButton(data.undo_count); render(); closeSettings(); toast('Settings saved');
}

async function restartServer() {
  try {
    const r = await fetch('/api/restart', { method: 'POST' });
    const data = await r.json();
    if (!data.ok) { toast('Restart error', true); return; }
    images = data.images || []; settings = data.settings || settings; currentIndex = 0; brightness = 1.0;
    
    $('source-folder').value = settings.source || ''; 
    $('not-nude-folder').value = settings.not_nude || ''; 
    $('nude-folder').value = settings.nude || '';
    $('label-not-nude').value = settings.label_not_nude || 'Not Nude';
    $('label-nude').value = settings.label_nude || 'Nude';
    
    updateUI();
    updateUndoButton(data.undo_count || 0); render(); closeSettings(); toast('Server restarted');
  } catch (e) { toast('Error: ' + e.message, true); }
}

function openSettings() { $('settings-modal').classList.add('open'); }
function closeSettings() { $('settings-modal').classList.remove('open'); }

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(err => {
      toast(`Error attempting to enable fullscreen: ${err.message}`, true);
    });
  } else {
    document.exitFullscreen();
  }
}

async function quitApp() {
  if (!confirm('Close application?')) return;
  try { await fetch('/api/quit', { method: 'POST' }); } catch (e) {}
  document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;color:#666;font-family:sans-serif;">Application closed. You can close the tab.</div>';
}

let toastTimer = null;
function toast(msg, isError) {
  const el = $('toast'); el.textContent = msg; el.classList.toggle('error', !!isError); el.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('show'), 2200);
}

(function setupSwipe() {
  const main = $('img-container'); let startX = 0, startY = 0, tracking = false;
  main.addEventListener('touchstart', (e) => {
    if (e.touches.length !== 1) { tracking = false; return; }
    if (e.target.closest('button')) { tracking = false; return; }
    startX = e.touches[0].clientX; startY = e.touches[0].clientY; tracking = true;
  }, { passive: true });
  main.addEventListener('touchend', (e) => {
    if (!tracking) return; tracking = false;
    if (scale > 1.0) return; // Отключаем свайп, если фото приближено
    const t = e.changedTouches[0]; const dx = t.clientX - startX; const dy = t.clientY - startY;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy)) return;
    if (dx < 0) nextImage(); else prevImage();
  }, { passive: true });
})();

$('btn-fullscreen').onclick = toggleFullscreen;
$('btn-settings').onclick = openSettings; $('btn-quit').onclick = quitApp; $('btn-back').onclick = prevImage; $('btn-undo').onclick = undoLast;
$('prev').onclick = prevImage; $('next').onclick = nextImage; $('b-up').onclick = () => changeBrightness(+0.1); $('b-down').onclick = () => changeBrightness(-0.1);
$('b-reset').onclick = resetBrightness; $('btn-not-nude').onclick = () => classify('not_nude'); $('btn-nude').onclick = () => classify('nude');
$('jump-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') jumpToImage(); });

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.ctrlKey && e.key === 'z') { undoLast(); e.preventDefault(); return; }
  if (e.key === 'ArrowRight') { nextImage(); e.preventDefault(); }
  else if (e.key === 'ArrowLeft') { prevImage(); e.preventDefault(); }
  else if (e.key === 'ArrowUp') { changeBrightness(+0.1); e.preventDefault(); }
  else if (e.key === 'ArrowDown') { changeBrightness(-0.1); e.preventDefault(); }
  else if (e.key === 'Delete') { deleteImage(); e.preventDefault(); }
  else if (e.key === '1') { classify('not_nude'); }
  else if (e.key === '2') { classify('nude'); }
  else if (e.key === 'r' || e.key === 'R') { resetBrightness(); }
  else if (e.key === 'f' || e.key === 'F') { toggleFullscreen(); }
  else if (e.key === 'Escape') { closeSettings(); }
});

loadState();
</script>
</body>
</html>'''

if __name__ == '__main__':
    main()