# Dependencies:
#   & 'C:\Program Files\Python314\python.exe' -m pip install --upgrade Flask Pillow requests send2trash pystray
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
import hashlib
from tkinter import filedialog, messagebox
from pathlib import Path
from collections import deque
import requests
import send2trash
import pystray
from PIL import Image, ImageTk

from flask import Flask, request, jsonify, send_file

# =====================================================================
# UPDATE SETTINGS
# =====================================================================
CURRENT_VERSION = "1.8.0"
GITHUB_USER = "mrfusionyt"
GITHUB_REPO = "-PV"
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
counter_label = None
update_btn = None
pending_update_url = None
quit_requested = threading.Event()
ignored_version = None
tray_icon = None
local_url_global = ""

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

def update_counter_ui():
    if counter_label:
        count = len(state['images'])
        counter_label.config(text=f"Remaining photos: {count}")

def refresh_source_and_ui():
    scan_source()
    if tk_root:
        tk_root.after(0, update_counter_ui)

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

# ----------------------- Tray Icon Logic -----------------------

def show_window(icon=None, item=None):
    tk_root.after(0, lambda: (tk_root.deiconify(), tk_root.lift(), tk_root.focus_force()))

def hide_window(icon=None, item=None):
    tk_root.withdraw()

def tray_quit(icon, item):
    icon.stop()
    quit_requested.set()
    tk_root.after(0, tk_root.destroy)

def create_tray():
    global tray_icon
    try:
        image = Image.open(resource_path('img/8080.png'))
        
        menu = pystray.Menu(
            pystray.MenuItem('Show App', show_window, default=True),
            pystray.MenuItem('Open in Browser', lambda: webbrowser.open(local_url_global)),
            pystray.MenuItem('Exit', tray_quit)
        )
        
        tray_icon = pystray.Icon("PhotoClassifier", image, "PhotoClassifier", menu)
        threading.Thread(target=tray_icon.run, daemon=True).start()
    except Exception as e:
        print("Failed to create tray icon:", e)

# ----------------------- Auto-Update Logic -----------------------

def check_for_updates(manual=False):
    global ignored_version
    if GITHUB_USER == "YOUR_NICKNAME" or GITHUB_REPO == "YOUR_REPO":
        if manual and tk_root:
            tk_root.after(0, lambda: messagebox.showinfo("Updates", "Auto-update is not configured.", parent=tk_root))
        return False

    try:
        url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/releases/latest"
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        data = r.json()
        
        latest_version = data.get('tag_name', '').lstrip('v')
        
        if latest_version and latest_version != CURRENT_VERSION and (manual or latest_version != ignored_version):
            download_url = None
            for asset in data.get('assets', []):
                if asset['name'].endswith('.zip'):
                    download_url = asset['browser_download_url']
                    break
            
            if not download_url:
                if manual and tk_root:
                    tk_root.after(0, lambda: messagebox.showerror("Update Error", f"Version {latest_version} found, but no .zip file attached to the release on GitHub.\n\nCheck if GitHub Actions finished successfully.", parent=tk_root))
                return False
                
            if tk_root:
                tk_root.after(0, lambda: show_update_indicator(latest_version, download_url))
            return True
        else:
            if manual and tk_root:
                tk_root.after(0, lambda: messagebox.showinfo("Updates", "You are already using the latest version.", parent=tk_root))
            return False
    except Exception as e:
        if manual and tk_root:
            tk_root.after(0, lambda: messagebox.showerror("Update Error", f"Failed to check updates:\n{e}", parent=tk_root))
        print(f"Update check error: {e}")
        return False

def force_check_update():
    def do_check():
        global ignored_version, pending_update_url
        ignored_version = None 
        pending_update_url = None
        
        if status_label:
            tk_root.after(0, lambda: status_label.config(text="Checking...", fg="#ffffff"))
        
        check_for_updates(manual=True)
        
        if status_label and not pending_update_url:
            tk_root.after(0, lambda: status_label.config(text="Application is running.", fg="#ffffff"))
    
    threading.Thread(target=do_check, daemon=True).start()

def update_loop():
    while not quit_requested.is_set():
        check_for_updates(manual=False)
        for _ in range(3600):
            if quit_requested.is_set(): break
            time.sleep(1)

def show_update_indicator(latest_version, download_url):
    global pending_update_url
    pending_update_url = download_url
    if update_btn and not update_btn.winfo_ismapped():
        update_btn.config(text=f"⬇ Update to v{latest_version}", bg="#2d7d46")
        update_btn.pack(pady=(5, 5))
        if status_label:
            status_label.config(text="Update Available!", fg="#ff3b5c")
        
        tk_root.update_idletasks()
        req_h = tk_root.winfo_reqheight()
        tk_root.geometry(f"460x{req_h}")

def trigger_update():
    global pending_update_url
    if not pending_update_url: return
    
    if update_btn:
        update_btn.config(state=tk.DISABLED, text="Downloading...", bg="#666666")
    if status_label:
        status_label.config(text="Downloading update...", fg="#ff3b5c")
        
    threading.Thread(target=download_and_update, args=(pending_update_url,), daemon=True).start()

def download_and_update(download_url):
    if not getattr(sys, 'frozen', False):
        tk_root.after(0, lambda: messagebox.showinfo("Info", "Auto-update only works in the compiled .exe version.", parent=tk_root))
        return
        
    try:
        exe_path = sys.executable
        exe_dir = os.path.dirname(exe_path)
        exe_name = os.path.basename(exe_path)
        
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
timeout /t 3 /nobreak > NUL
taskkill /IM "{exe_name}" /F > NUL 2>&1
robocopy "{update_dir}" "{exe_dir}" /E /IS /IT /NFL /NDL /NJH /NJS /NC /NS /NP > NUL
rmdir /s /q "{update_dir}"
del "{zip_path}"
start "" "{exe_dir}\\{exe_name}"
del "%~f0"
""")

        subprocess.Popen(f'"{bat_path}"', shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
        quit_requested.set()
        if tray_icon:
            tray_icon.stop()

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
    refresh_source_and_ui()
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
    refresh_source_and_ui()
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
            img = Image.open(path)
            if img.width <= 1920 and img.height <= 1920:
                return send_file(path)
            
            cache_dir = os.path.join(state['source'], '.cache')
            os.makedirs(cache_dir, exist_ok=True)
            
            h = hashlib.md5(path.encode('utf-8')).hexdigest()
            cache_path = os.path.join(cache_dir, h + ".jpg")
            
            if os.path.exists(cache_path):
                return send_file(cache_path, mimetype='image/jpeg')
                
            img.thumbnail((1920, 1920), Image.BILINEAR) 
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(cache_path, format='JPEG', quality=80)
            return send_file(cache_path, mimetype='image/jpeg')
        except Exception:
            return send_file(path)
            
    return send_file(path)

@app.route('/api/info')
def api_info():
    name = request.args.get('name', '')
    if not is_safe_name(name) or not state['source']: return jsonify({'ok': False})
    path = os.path.join(state['source'], name)
    if not os.path.isfile(path): return jsonify({'ok': False})
    
    size_mb = os.path.getsize(path) / (1024 * 1024)
    try:
        with Image.open(path) as img:
            w, h = img.size
        return jsonify({'ok': True, 'size': f"{size_mb:.1f} MB", 'res': f"{w}x{h}"})
    except Exception:
        return jsonify({'ok': True, 'size': f"{size_mb:.1f} MB", 'res': "?"})

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
    refresh_source_and_ui()
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

    refresh_source_and_ui()
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
        send2trash.send2trash(path)
    except Exception as e: 
        return jsonify({'ok': False, 'error': str(e)})
    refresh_source_and_ui()
    with moves_lock: undo_count = len(moves)
    return jsonify({'ok': True, 'images': state['images'], 'undo_count': undo_count})

@app.route('/api/quit', methods=['POST'])
def api_quit():
    if tray_icon: tray_icon.stop()
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
    global tk_root, status_label, counter_label, update_btn, local_url_global

    load_config()
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if os.path.isfile(arg): state['source'] = os.path.dirname(os.path.abspath(arg)); save_config()
        elif os.path.isdir(arg): state['source'] = os.path.abspath(arg); save_config()

    scan_source()
    port = find_free_port()
    local_url = f'http://127.0.0.1:{port}'
    local_url_global = local_url
    lan_ips = get_local_ips()

    tk_root = tk.Tk()
    tk_root.title('PhotoClassifier')
    tk_root.resizable(False, False)
    tk_root.configure(bg='#141418')

    try:
        icon_img = tk.PhotoImage(file=resource_path('img/8080.png'))
        tk_root.iconphoto(True, icon_img)
    except Exception: pass

    status_label = tk.Label(tk_root, text='Application is running.', bg='#141418', fg='#ffffff', font=('Segoe UI', 12, 'bold'))
    status_label.pack(pady=(10, 2))
    
    tk.Label(tk_root, text=f'Version {CURRENT_VERSION}', bg='#141418', fg='#666666', font=('Segoe UI', 9)).pack()
    
    counter_label = tk.Label(tk_root, text="Remaining photos: 0", bg='#141418', fg='#aaaaaa', font=('Segoe UI', 10))
    counter_label.pack(pady=(5, 2))
    update_counter_ui()

    check_upd_btn = tk.Button(tk_root, text="🔍 Check for Updates", command=force_check_update, bg='#2a2a2a', fg='#ffffff', relief='flat', font=('Segoe UI', 8), cursor='hand2')
    check_upd_btn.pack(pady=(0, 5))

    update_frame = tk.Frame(tk_root, bg='#141418')
    update_frame.pack()

    update_btn = create_dark_button(update_frame, '', trigger_update, is_primary=True)
    update_btn.config(bg="#2d7d46", activebackground="#369b56")
    update_btn.bind('<Enter>', lambda e: update_btn.config(bg='#369b56'))
    update_btn.bind('<Leave>', lambda e: update_btn.config(bg='#2d7d46'))

    if lan_ips:
        tk.Label(tk_root, text='From local network / VPN:', bg='#141418', fg='#888888', font=('Segoe UI', 9)).pack(pady=(10, 0))
        urls_frame = tk.Frame(tk_root, bg='#1e1e24', padx=15, pady=10)
        urls_frame.pack(pady=(5, 10), fill='x', padx=40)
        for ip in lan_ips:
            tk.Label(urls_frame, text=f'http://{ip}:{port}', bg='#1e1e24', fg='#ff3b5c', font=('Consolas', 11)).pack(anchor='w')

    btn_frame = tk.Frame(tk_root, bg='#141418')
    btn_frame.pack(pady=(5, 10))

    def reload_server_ui():
        load_config()
        scan_source()
        update_counter_ui()
        messagebox.showinfo("Info", "Server restarted / Folder reloaded", parent=tk_root)

    create_dark_button(btn_frame, 'Restart', reload_server_ui).pack(side=tk.LEFT, padx=5)
    create_dark_button(btn_frame, 'Open', lambda: webbrowser.open(local_url), is_primary=True).pack(side=tk.LEFT, padx=5)
    create_dark_button(btn_frame, 'Hide', hide_window).pack(side=tk.LEFT, padx=5)
    create_dark_button(btn_frame, 'Exit', lambda: (tray_icon.stop(), quit_requested.set())).pack(side=tk.LEFT, padx=5)

    tk_root.update_idletasks()
    req_h = tk_root.winfo_reqheight()
    tk_root.geometry(f"460x{req_h}")

    tk_root.protocol('WM_DELETE_WINDOW', hide_window)

    def run_flask():
        try: app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)
        except Exception as e: print('Flask error:', e)

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()
    
    create_tray()

    tk_root.after(100, tk_pump)
    
    try:
        tk_root.mainloop()
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
        quit_requested.set()
    finally:
        if tray_icon:
            try: tray_icon.stop()
            except: pass
        try:
            tk_root.destroy()
        except:
            pass

HTML_PAGE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>PhotoClassifier</title>
<link rel="icon" type="image/png" href="/favicon.ico">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

  :root {
    --bg-app: #18181b;
    --bg-sidebar: #141417;
    --bg-surface: #27272a;
    --bg-surface-hover: #3f3f46;
    --text-main: #f4f4f5;
    --text-muted: #a1a1aa;
    --accent: #ef4444;
    --accent-hover: #dc2626;
    --border: #27272a;
    --green: #22c55e;
    --green-hover: #16a34a;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; overflow: hidden; font-family: 'Inter', -apple-system, sans-serif; background: var(--bg-app); color: var(--text-main); }
  
  .app-container { display: flex; height: 100vh; width: 100vw; }

  /* --- SIDEBAR --- */
  .sidebar {
    width: 260px;
    background: var(--bg-sidebar);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    padding: 20px 0;
    flex-shrink: 0;
  }
  .profile-card {
    margin: 0 15px 30px 15px;
    padding: 12px;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    display: flex;
    align-items: center;
    gap: 12px;
    cursor: pointer;
  }
  .profile-card:hover { background: var(--bg-surface-hover); }
  .avatar {
    width: 36px; height: 36px; border-radius: 50%;
    background: var(--accent);
    display: flex; align-items: center; justify-content: center;
    font-weight: bold; color: white;
  }
  .profile-info { font-size: 13px; }
  .profile-info b { display: block; font-size: 15px; color: var(--text-main); }
  .profile-info span { color: var(--text-muted); }

  .nav-section { margin-bottom: 20px; }
  .nav-section h3 {
    font-size: 12px; text-transform: uppercase; color: var(--text-muted);
    margin: 0 25px 10px 25px; font-weight: 600; letter-spacing: 0.5px;
  }
  .nav-list { list-style: none; }
  .nav-list li {
    padding: 10px 25px;
    font-size: 14px;
    color: var(--text-muted);
    cursor: pointer;
    transition: all 0.2s;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .nav-list li:hover { color: var(--text-main); background: rgba(255,255,255,0.03); }
  .nav-list li.active { background: var(--accent); color: white; font-weight: 500; }
  .nav-list li.active:hover { background: var(--accent-hover); }
  .nav-list li.danger { color: var(--accent); }
  .nav-list li.danger:hover { color: white; background: var(--accent); }

  /* --- MAIN CONTENT --- */
  .main-content {
    flex-grow: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  /* --- TOP NAV --- */
  .top-nav {
    height: 60px;
    background: var(--bg-sidebar);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 24px;
    flex-shrink: 0;
  }
  .nav-tabs { display: flex; gap: 24px; height: 100%; }
  .nav-tab {
    height: 100%;
    display: flex;
    align-items: center;
    color: var(--text-muted);
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    position: relative;
  }
  .nav-tab.active { color: var(--text-main); }
  .nav-tab.active::after {
    content: ''; position: absolute; bottom: 0; left: 0; right: 0; height: 3px; background: var(--accent);
  }
  
  .top-actions { display: flex; align-items: center; gap: 12px; }
  .top-actions span { font-family: monospace; font-size: 15px; color: var(--text-muted); }
  
  .btn {
    background: var(--bg-surface);
    color: var(--text-main);
    border: 1px solid var(--border);
    padding: 8px 16px; border-radius: 6px;
    font-size: 13px; font-weight: 500;
    cursor: pointer;
    transition: all 0.2s;
    display: inline-flex; align-items: center; justify-content: center;
  }
  .btn:hover { background: var(--bg-surface-hover); }
  .btn-primary { background: var(--accent); border-color: var(--accent); color: white; }
  .btn-primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }
  .btn-icon { padding: 8px 10px; font-size: 16px; }

  /* --- WORKSPACE --- */
  .workspace {
    flex-grow: 1;
    display: flex;
    flex-direction: column;
    padding: 16px 24px;
    min-height: 0;
  }

  .viewer-panel {
    flex-grow: 1;
    background: #000;
    border-radius: 8px;
    border: 1px solid var(--border);
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    margin-bottom: 16px;
  }

  #photo {
    max-width: 100%; max-height: 100%; object-fit: contain;
    display: none; user-select: none; -webkit-user-drag: none;
    transition: filter 0.1s linear; will-change: filter, transform;
    transform-origin: center center;
  }
  #photo-placeholder { color: var(--text-muted); font-size: 16px; text-align: center; }

  .nav {
    position: absolute; top: 50%; transform: translateY(-50%);
    background: rgba(0,0,0,0.5); color: white; border: 1px solid rgba(255,255,255,0.1);
    width: 60px; height: 120px; font-size: 36px; border-radius: 8px;
    cursor: pointer; opacity: 0.7; transition: all 0.2s;
    display: flex; align-items: center; justify-content: center; z-index: 10;
  }
  .nav:hover { opacity: 1; background: rgba(0,0,0,0.8); }
  #prev { left: 16px; }
  #next { right: 16px; }

  .toolbar {
    display: flex; align-items: center; gap: 12px;
    background: var(--bg-sidebar); border: 1px solid var(--border);
    padding: 12px 16px; border-radius: 8px;
  }
  .toolbar button {
    background: var(--bg-surface); border: 1px solid var(--border);
    color: var(--text-main); width: 40px; height: 40px; border-radius: 6px;
    font-size: 18px; cursor: pointer; display: flex; align-items: center; justify-content: center;
  }
  .toolbar button:hover { background: var(--bg-surface-hover); }
  #brightness-value { font-family: monospace; font-size: 15px; color: var(--text-muted); min-width: 50px; text-align: center; }

  /* --- BOTTOM ACTION BUTTONS --- */
  .action-bar {
    display: flex; gap: 16px; margin-top: 16px;
  }
  .action-btn {
    flex: 1;
    padding: 24px;
    font-size: 32px; font-weight: 700; letter-spacing: 1px;
    border: none; border-radius: 8px;
    cursor: pointer; color: white;
    transition: transform 0.1s, filter 0.15s;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
  }
  .action-btn:active { transform: scale(0.98); }
  .action-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  #btn-not-nude { background: var(--accent); }
  #btn-not-nude:hover:not(:disabled) { background: var(--accent-hover); }
  #btn-nude { background: var(--green); }
  #btn-nude:hover:not(:disabled) { background: var(--green-hover); }

  /* --- MODAL (Settings) --- */
  .modal-bg {
    position: fixed; inset: 0; background: rgba(0,0,0,0.8); backdrop-filter: blur(4px);
    display: none; align-items: center; justify-content: center; z-index: 100; padding: 16px;
  }
  .modal-bg.open { display: flex; }
  .modal {
    background: var(--bg-sidebar); border: 1px solid var(--border); border-radius: 12px;
    padding: 24px; width: 800px; max-width: 100%; max-height: 90vh; overflow-y: auto;
    box-shadow: 0 20px 40px rgba(0,0,0,0.5);
  }
  .modal h2 { font-size: 20px; font-weight: 600; margin-bottom: 20px; }
  
  .settings-table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
  .settings-table th {
    text-align: left; padding: 12px; font-size: 12px; color: var(--text-muted);
    text-transform: uppercase; border-bottom: 1px solid var(--border); font-weight: 600;
  }
  .settings-table td {
    padding: 12px; border-bottom: 1px solid var(--border); font-size: 14px;
  }
  .settings-table input {
    width: 100%; background: var(--bg-surface); border: 1px solid var(--border);
    padding: 8px 12px; border-radius: 6px; color: var(--text-main); font-family: monospace;
  }
  .settings-table input:focus { outline: none; border-color: var(--accent); }
  .settings-table button {
    background: var(--bg-surface); border: 1px solid var(--border);
    color: var(--text-main); padding: 8px 12px; border-radius: 6px; cursor: pointer;
  }
  .settings-table button:hover { background: var(--bg-surface-hover); }

  .modal-actions { display: flex; justify-content: flex-end; gap: 12px; }

  /* --- TOAST --- */
  #toast { position: fixed; top: 80px; left: 50%; transform: translateX(-50%); background: var(--bg-surface); border: 1px solid var(--border); color: var(--text-main); padding: 12px 24px; border-radius: 8px; font-size: 14px; opacity: 0; transition: opacity 0.2s; pointer-events: none; z-index: 200; text-align: center; box-shadow: 0 10px 20px rgba(0,0,0,0.5); }
  #toast.show { opacity: 1; }
  #toast.error { border-color: var(--accent); color: var(--accent); }

  @media (max-width: 768px) {
    .app-container { flex-direction: column; }
    .sidebar { width: 100%; height: auto; padding: 10px; flex-direction: row; align-items: center; justify-content: space-between; border-right: none; border-bottom: 1px solid var(--border); }
    .profile-card { margin: 0; padding: 8px; display: none; }
    .nav-section { margin: 0; }
    .nav-section h3 { display: none; }
    .nav-list { display: flex; gap: 8px; }
    .nav-list li { padding: 8px 12px; border-radius: 6px; }
    .nav-list li.active { background: var(--accent); }
    
    .top-nav { padding: 0 12px; height: 50px; }
    .nav-tabs { display: none; }
    .top-actions span { display: none; }
    
    .workspace { padding: 8px; }
    .nav { width: 44px; height: 80px; font-size: 28px; }
    #prev { left: 8px; } #next { right: 8px; }
    
    .action-bar { flex-direction: column; gap: 8px; }
    .action-btn { padding: 16px; font-size: 24px; }
  }
</style>
</head>
<body>
  <div id="progress-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 3px; background: transparent; z-index: 1000;"><div id="progress-bar" style="height: 100%; background: var(--accent); width: 0%; transition: width 0.3s ease;"></div></div>

  <div class="app-container">
    <!-- SIDEBAR -->
    <aside class="sidebar">
      <div class="profile-card">
        <div class="avatar">📷</div>
        <div class="profile-info">
          <span>Profile</span><br>
          <b>PhotoClassifier</b>
        </div>
      </div>

      <div class="nav-section">
        <h3>Settings</h3>
        <ul class="nav-list">
          <li class="active" onclick="openSettings()">📁 Libraries</li>
        </ul>
      </div>

      <div class="nav-section">
        <h3>Tools</h3>
        <ul class="nav-list">
          <li onclick="undoLast()">↶ Undo Action</li>
          <li onclick="toggleFullscreen()">⛶ Fullscreen</li>
          <li onclick="quitApp()" class="danger">✕ Close Application</li>
        </ul>
      </div>
    </aside>

    <!-- MAIN CONTENT -->
    <main class="main-content">
      <header class="top-nav">
        <div class="nav-tabs">
          <div class="nav-tab active">Photos</div>
        </div>
        <div class="top-actions">
          <span id="counter">0 / 0</span>
          <button class="btn" id="btn-undo" disabled>↶ Undo</button>
        </div>
      </header>

      <div class="workspace">
        <div class="viewer-panel" id="img-container">
          <button id="prev" class="nav" title="Previous">‹</button>
          <button id="next" class="nav" title="Next">›</button>
          <img id="photo" alt="" draggable="false">
          <div id="photo-placeholder">Click "Libraries" in the sidebar to select a folder</div>
        </div>

        <div class="toolbar">
          <button id="b-down" title="Darker">☀−</button>
          <span id="brightness-value">1.00</span>
          <button id="b-up" title="Brighter">☀+</button>
          <button id="b-reset" title="Reset">↺</button>
        </div>

        <div class="action-bar">
          <button class="action-btn" id="btn-not-nude" disabled>Not Nude</button>
          <button class="action-btn" id="btn-nude" disabled>Nude</button>
        </div>
      </div>
    </main>
  </div>

  <!-- SETTINGS MODAL -->
  <div class="modal-bg" id="settings-modal">
    <div class="modal">
      <h2>Folder Libraries</h2>
      
      <table class="settings-table">
        <thead>
          <tr>
            <th style="width: 50px;">#</th>
            <th>Role</th>
            <th>Path</th>
            <th style="width: 100px;">Action</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>1</td>
            <td>Source</td>
            <td><input type="text" id="source-folder" placeholder="Select source folder..."></td>
            <td><button onclick="pickFolder('source')">Browse</button></td>
          </tr>
          <tr>
            <td>2</td>
            <td id="label-not-nude-td">Not Nude</td>
            <td><input type="text" id="not-nude-folder" placeholder="Select target folder..."></td>
            <td><button onclick="pickFolder('not_nude')">Browse</button></td>
          </tr>
          <tr>
            <td>3</td>
            <td id="label-nude-td">Nude</td>
            <td><input type="text" id="nude-folder" placeholder="Select target folder..."></td>
            <td><button onclick="pickFolder('nude')">Browse</button></td>
          </tr>
        </tbody>
      </table>

      <div style="display: flex; gap: 16px; margin-bottom: 20px;">
        <div style="flex: 1;">
          <label style="font-size: 12px; color: var(--text-muted); margin-bottom: 4px; display: block;">Red Button Name</label>
          <input type="text" id="label-not-nude" style="width: 100%; background: var(--bg-surface); border: 1px solid var(--border); padding: 8px 12px; border-radius: 6px; color: white;">
        </div>
        <div style="flex: 1;">
          <label style="font-size: 12px; color: var(--text-muted); margin-bottom: 4px; display: block;">Green Button Name</label>
          <input type="text" id="label-nude" style="width: 100%; background: var(--bg-surface); border: 1px solid var(--border); padding: 8px 12px; border-radius: 6px; color: white;">
        </div>
      </div>

      <div class="modal-actions">
        <button class="btn" onclick="closeSettings()">Cancel</button>
        <button class="btn btn-primary" onclick="saveSettings()">Save Changes</button>
      </div>
    </div>
  </div>

  <div id="toast"></div>

<script>
let images = [];
let currentIndex = 0;
let settings = { source: '', not_nude: '', nude: '', label_not_nude: 'Not Nude', label_nude: 'Nude' };
let brightness = 1.0;
let scale = 1.0, translateX = 0, translateY = 0;

const $ = (id) => document.getElementById(id);

async function loadState() {
  const r = await fetch('/api/state');
  const data = await r.json();
  images = data.images || [];
  
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
  $('label-not-nude-td').textContent = settings.label_not_nude || 'Not Nude';
  $('label-nude-td').textContent = settings.label_nude || 'Nude';
}

function updateUndoButton(count) {
  const btn = $('btn-undo');
  btn.textContent = '↶ Undo (' + count + ')';
  btn.disabled = count === 0;
}

async function render() {
  const img = $('photo'); const ph = $('photo-placeholder'); const counter = $('counter'); const btnNN = $('btn-not-nude'); const btnN = $('btn-nude');
  const progressBar = $('progress-bar');
  
  scale = 1.0; translateX = 0; translateY = 0;
  applyTransform();

  if (images.length === 0) {
    img.style.display = 'none'; img.removeAttribute('src'); ph.style.display = 'block';
    ph.textContent = settings.source ? 'No images in the selected folder (or all processed)' : 'Click "Libraries" in the sidebar to select a folder';
    counter.textContent = ''; btnNN.disabled = true; btnN.disabled = true; applyBrightness(); 
    progressBar.style.width = '0%';
    return;
  }
  if (currentIndex < 0) currentIndex = images.length - 1;
  if (currentIndex >= images.length) currentIndex = images.length - 1;
  
  const name = images[currentIndex];
  
  img.src = '/api/image?name=' + encodeURIComponent(name) + '&thumb=1';
  
  img.style.display = 'block'; ph.style.display = 'none';
  
  counter.textContent = (currentIndex + 1) + ' / ' + images.length; 
  counter.title = name; 
  
  btnNN.disabled = false; btnN.disabled = false; applyBrightness();
  
  const progressPercent = ((currentIndex + 1) / images.length) * 100;
  progressBar.style.width = progressPercent + '%';

  localStorage.setItem('lastPhotoIndex', currentIndex);

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

function applyTransform() {
    const img = $('photo');
    if (img) {
        img.style.transform = `translate(${translateX}px, ${translateY}px) scale(${scale})`;
        if (scale > 1.0) $('img-container').style.cursor = 'grab';
        else $('img-container').style.cursor = 'default';
    }
}

// ---------------- Pan & Zoom Logic ----------------
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

let isDragging = false, dragStartX = 0, dragStartY = 0;
$('img-container').addEventListener('mousedown', (e) => {
    if (scale > 1.0) {
        e.preventDefault(); isDragging = true;
        dragStartX = e.clientX - translateX; dragStartY = e.clientY - translateY;
        $('img-container').style.cursor = 'grabbing';
    }
});
window.addEventListener('mousemove', (e) => {
    if (isDragging) {
        e.preventDefault();
        translateX = e.clientX - dragStartX; translateY = e.clientY - dragStartY;
        applyTransform();
    }
});
window.addEventListener('mouseup', () => {
    if (isDragging) { isDragging = false; $('img-container').style.cursor = 'grab'; }
});

// Мобилки: Свайпы, Зум, Перетаскивание
let touchMode = 'none', touchStartX = 0, touchStartY = 0;
let dragStartTouchX = 0, dragStartTouchY = 0, initialDistance = 0;

$('img-container').addEventListener('touchstart', (e) => {
    if (e.touches.length === 1) {
        touchStartX = e.touches[0].clientX; touchStartY = e.touches[0].clientY;
        if (scale > 1.0) {
            touchMode = 'drag';
            dragStartTouchX = e.touches[0].clientX - translateX;
            dragStartTouchY = e.touches[0].clientY - translateY;
        } else { touchMode = 'swipe'; }
    } else if (e.touches.length === 2) {
        touchMode = 'pinch';
        initialDistance = Math.hypot(e.touches[0].pageX - e.touches[1].pageX, e.touches[0].pageY - e.touches[1].pageY);
    }
}, { passive: false });

$('img-container').addEventListener('touchmove', (e) => {
    if (touchMode === 'drag' && e.touches.length === 1) {
        e.preventDefault();
        translateX = e.touches[0].clientX - dragStartTouchX;
        translateY = e.touches[0].clientY - dragStartTouchY;
        applyTransform();
    } else if (touchMode === 'pinch' && e.touches.length === 2) {
        e.preventDefault();
        const currentDistance = Math.hypot(e.touches[0].pageX - e.touches[1].pageX, e.touches[0].pageY - e.touches[1].pageY);
        if (initialDistance > 0) {
            scale = Math.max(1.0, Math.min(5.0, scale * (currentDistance / initialDistance)));
            if (scale === 1.0) { translateX = 0; translateY = 0; }
            applyTransform();
            initialDistance = currentDistance;
        }
    }
}, { passive: false });

$('img-container').addEventListener('touchend', (e) => {
    if (touchMode === 'swipe') {
        const t = e.changedTouches[0]; const dx = t.clientX - touchStartX; const dy = t.clientY - touchStartY;
        if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) { if (dx < 0) nextImage(); else prevImage(); }
    }
    if (e.touches.length === 0) touchMode = 'none';
}, { passive: false });

// ---------------- API Functions ----------------

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
  const titles = { 'source': 'Select source folder', 'not_nude': 'Select target folder 1', 'nude': 'Select target folder 2' };
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

function openSettings() { $('settings-modal').classList.add('open'); }
function closeSettings() { $('settings-modal').classList.remove('open'); }

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(err => toast(`Error: ${err.message}`, true));
  } else { document.exitFullscreen(); }
}

async function quitApp() {
  if (!confirm('Close application?')) return;
  try { await fetch('/api/quit', { method: 'POST' }); } catch (e) {}
  document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;color:#666;font-family:sans-serif;">Application closed.</div>';
}

let toastTimer = null;
function toast(msg, isError) {
  const el = $('toast'); el.textContent = msg; el.classList.toggle('error', !!isError); el.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('show'), 2200);
}

$('btn-settings').onclick = openSettings; $('btn-quit').onclick = quitApp; 
$('btn-undo').onclick = undoLast;
$('prev').onclick = prevImage; $('next').onclick = nextImage; $('b-up').onclick = () => changeBrightness(+0.1); $('b-down').onclick = () => changeBrightness(-0.1);
$('b-reset').onclick = resetBrightness; $('btn-not-nude').onclick = () => classify('not_nude'); $('btn-nude').onclick = () => classify('nude');

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