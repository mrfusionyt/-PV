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
CURRENT_VERSION = "1.8.1"
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
src_label = None
nn_label = None
n_label = None
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
    if src_label:
        src_label.config(text=f"Source: {state['source'] or 'Not set'}")
    if nn_label:
        nn_label.config(text=f"{state['labels']['not_nude']}: {state['targets']['not_nude'] or 'Not set'}")
    if n_label:
        n_label.config(text=f"{state['labels']['nude']}: {state['targets']['nude'] or 'Not set'}")

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
        if ip not in ips and not ip.startswith('127.0.0.0') and ip != '0.0.0.0':
            ips.append(ip)
    except Exception: pass
    return ips

def tk_undo_action():
    try:
        r = requests.post(f'{local_url_global}/api/undo')
        if r.ok:
            data = r.json()
            if data.get('ok'):
                load_config()
                scan_source()
                update_counter_ui()
    except: pass

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
            
            if tk_root and download_url:
                tk_root.after(0, lambda: show_update_indicator(latest_version, download_url))
            return True
        return False
    except Exception as e:
        print(f"Update check error: {e}")
        return False

def force_check_update():
    def do_check():
        global ignored_version, pending_update_url
        ignored_version = None 
        pending_update_url = None
        
        if status_label:
            tk_root.after(0, lambda: status_label.config(text="Checking...", fg="#ffffff"))
        
        found = check_for_updates(manual=True)
        
        if not found and tk_root:
            def show_latest_msg():
                if status_label:
                    status_label.config(text="You are already using the latest version.", fg="#22c55e") # Зеленый цвет
                
                # Возвращаем исходный текст через 20 секунд
                def revert_msg():
                    if status_label and status_label.cget("text") == "You are already using the latest version.":
                        status_label.config(text="Application is running.", fg="#ffffff")
                tk_root.after(20000, revert_msg)
            
            tk_root.after(0, show_latest_msg)
        elif not found and status_label:
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
        tk_root.geometry(f"800x{req_h + 50}")

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
    bg = '#ef4444' if is_primary else '#27272a'
    fg = '#ffffff'
    active_bg = '#dc2626' if is_primary else '#3f3f46'
    btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=active_bg, activeforeground=fg, font=('Segoe UI', 10, 'bold' if is_primary else 'normal'), relief='flat', bd=0, padx=20, pady=8, cursor='hand2')
    if not is_primary:
        btn.bind('<Enter>', lambda e: btn.config(bg='#3f3f46'))
        btn.bind('<Leave>', lambda e: btn.config(bg='#27272a'))
    else:
        btn.bind('<Enter>', lambda e: btn.config(bg='#dc2626'))
        btn.bind('<Leave>', lambda e: btn.config(bg='#ef4444'))
    return btn

def main():
    global tk_root, status_label, counter_label, src_label, nn_label, n_label, update_btn, local_url_global

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
    tk_root.geometry('800x420')
    tk_root.configure(bg='#18181b')

    try:
        icon_img = tk.PhotoImage(file=resource_path('img/8080.png'))
        tk_root.iconphoto(True, icon_img)
    except Exception: pass

    # === ЛЕВАЯ ПАНЕЛЬ (Sidebar) ===
    sidebar = tk.Frame(tk_root, width=220, bg='#141417')
    sidebar.pack(side=tk.LEFT, fill=tk.Y)
    sidebar.pack_propagate(False)

    # Профиль
    profile_frame = tk.Frame(sidebar, bg='#27272a', pady=10, padx=10)
    profile_frame.pack(fill=tk.X, padx=15, pady=(20, 30))
    tk.Label(profile_frame, text='📷', bg='#27272a', fg='white', font=('Segoe UI', 16)).pack(side=tk.LEFT, padx=(0, 10))
    profile_text = tk.Frame(profile_frame, bg='#27272a')
    profile_text.pack(side=tk.LEFT)
    tk.Label(profile_text, text='PROFILE', bg='#27272a', fg='#a1a1aa', font=('Segoe UI', 8)).pack(anchor='w')
    tk.Label(profile_text, text='PhotoClassifier', bg='#27272a', fg='white', font=('Segoe UI', 11, 'bold')).pack(anchor='w')

    # Настройки
    tk.Label(sidebar, text='SETTINGS', bg='#141417', fg='#a1a1aa', font=('Segoe UI', 9, 'bold')).pack(anchor='w', padx=20, pady=(0, 10))
    btn_libs = tk.Button(sidebar, text='📁 Libraries', bg='#ef4444', fg='white', relief='flat', anchor='w', padx=20, pady=8, font=('Segoe UI', 10, 'bold'), cursor='hand2')
    btn_libs.pack(fill=tk.X, pady=2)

    # Инструменты
    tk.Label(sidebar, text='TOOLS', bg='#141417', fg='#a1a1aa', font=('Segoe UI', 9, 'bold')).pack(anchor='w', padx=20, pady=(20, 10))
    
    btn_undo = tk.Button(sidebar, text='↶ Undo Action', bg='#141417', fg='#a1a1aa', relief='flat', anchor='w', padx=20, pady=6, font=('Segoe UI', 10), cursor='hand2', command=tk_undo_action)
    btn_undo.pack(fill=tk.X)
    btn_undo.bind('<Enter>', lambda e: btn_undo.config(fg='white', bg='#27272a'))
    btn_undo.bind('<Leave>', lambda e: btn_undo.config(fg='#a1a1aa', bg='#141417'))

    btn_full = tk.Button(sidebar, text='⛶ Fullscreen', bg='#141417', fg='#a1a1aa', relief='flat', anchor='w', padx=20, pady=6, font=('Segoe UI', 10), cursor='hand2', command=lambda: webbrowser.open(local_url_global))
    btn_full.pack(fill=tk.X)
    btn_full.bind('<Enter>', lambda e: btn_full.config(fg='white', bg='#27272a'))
    btn_full.bind('<Leave>', lambda e: btn_full.config(fg='#a1a1aa', bg='#141417'))

    btn_close = tk.Button(sidebar, text='✕ Close Application', bg='#141417', fg='#ef4444', relief='flat', anchor='w', padx=20, pady=6, font=('Segoe UI', 10), cursor='hand2', command=lambda: (tray_icon.stop(), quit_requested.set()))
    btn_close.pack(fill=tk.X, pady=(20, 0))
    btn_close.bind('<Enter>', lambda e: btn_close.config(fg='white', bg='#ef4444'))
    btn_close.bind('<Leave>', lambda e: btn_close.config(fg='#ef4444', bg='#141417'))

    # === ОСНОВНАЯ ЧАСТЬ ===
    main_content = tk.Frame(tk_root, bg='#18181b')
    main_content.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

    # Заголовок и счетчик
    status_label = tk.Label(main_content, text='Application is running.', bg='#18181b', fg='white', font=('Segoe UI', 18, 'bold'))
    status_label.pack(anchor='w', padx=24, pady=(24, 5))

    counter_label = tk.Label(main_content, text='Remaining photos: 0', bg='#18181b', fg='#a1a1aa', font=('Segoe UI', 12))
    counter_label.pack(anchor='w', padx=24, pady=(0, 15))

    # Карточка с путями (стилизована под таблицу)
    table_frame = tk.Frame(main_content, bg='#141417', padx=20, pady=15)
    table_frame.pack(fill=tk.X, padx=24, pady=(0, 15))

    tk.Label(table_frame, text='Folder Paths:', bg='#141417', fg='#a1a1aa', font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(0, 10))
    
    src_label = tk.Label(table_frame, text='Source: ...', bg='#141417', fg='#a1a1aa', font=('Consolas', 10))
    src_label.pack(anchor='w', pady=2)
    nn_label = tk.Label(table_frame, text='Not Nude: ...', bg='#141417', fg='#a1a1aa', font=('Consolas', 10))
    nn_label.pack(anchor='w', pady=2)
    n_label = tk.Label(table_frame, text='Nude: ...', bg='#141417', fg='#a1a1aa', font=('Consolas', 10))
    n_label.pack(anchor='w', pady=2)
    
    update_counter_ui()

    update_frame = tk.Frame(main_content, bg='#18181b')
    update_frame.pack(padx=24, pady=(0, 10))
    update_btn = create_dark_button(update_frame, '', trigger_update, is_primary=True)
    update_btn.config(bg="#22c55e", activebackground="#16a34a")
    update_btn.bind('<Enter>', lambda e: update_btn.config(bg='#16a34a'))
    update_btn.bind('<Leave>', lambda e: update_btn.config(bg='#22c55e'))

    # Нижняя панель с кнопками (теперь прижата вплотную к таблице, без пустоты)
    bottom_bar = tk.Frame(main_content, bg='#18181b')
    bottom_bar.pack(fill=tk.X, padx=24, pady=(10, 20))

    btn_check = tk.Button(bottom_bar, text='Check for Updates', command=force_check_update, bg='#27272a', fg='white', relief='flat', padx=20, pady=10, font=('Segoe UI', 10), cursor='hand2')
    btn_check.pack(side=tk.LEFT, padx=(0, 10))

    def reload_server_ui():
        load_config()
        scan_source()
        update_counter_ui()
        messagebox.showinfo("Info", "Server restarted / Folder reloaded", parent=tk_root)

    btn_restart = tk.Button(bottom_bar, text='Restart Server', bg='#27272a', fg='white', relief='flat', padx=20, pady=10, font=('Segoe UI', 10), cursor='hand2', command=reload_server_ui)
    btn_restart.pack(side=tk.LEFT, padx=(0, 10))

    btn_open = tk.Button(bottom_bar, text='Open in Browser', bg='#ef4444', fg='white', relief='flat', padx=20, pady=10, font=('Segoe UI', 10, 'bold'), cursor='hand2', command=lambda: webbrowser.open(local_url_global))
    btn_open.pack(side=tk.LEFT)

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
  
  #photo { max-width: 100%; max-height: 100%; object-fit: contain; display: none; user-select: none; -webkit-user-drag: none; transition: filter 0.1s linear; will-change: filter, transform; transform-origin: center center; }
  
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
  
  #counter { 
    margin-left: auto; 
    font-size: 18px; 
    color: #ddd; 
    font-family: Consolas, monospace; 
    padding-left: 12px; 
    white-space: nowrap; 
  }
  
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
    #counter { font-size: 16px; }
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
    #counter { font-size: 14px; }
  }
</style>
</head>
<body>
  <div id="progress-container"><div id="progress-bar"></div></div>
  
  <header>
    <span class="title">PhotoClassifier</span>
    <span class="spacer"></span>
    <button id="btn-fullscreen" title="Toggle Fullscreen (F)">⛶</button>
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
          <input type="text" id="source-folder" placeholder="Type path or Browse...">
          <button onclick="pickFolder('source')">Browse...</button>
        </div>
      </div>
      <div class="field">
        <label>Save Button 1 photos to</label>
        <div class="row">
          <input type="text" id="not-nude-folder" placeholder="Type path or Browse...">
          <button onclick="pickFolder('not_nude')">Browse...</button>
        </div>
      </div>
      <div class="field">
        <label>Save Button 2 photos to</label>
        <div class="row">
          <input type="text" id="nude-folder" placeholder="Type path or Browse...">
          <button onclick="pickFolder('nude')">Browse...</button>
        </div>
      </div>
      <div class="actions">
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

let scale = 1.0;
let translateX = 0, translateY = 0;

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
}

function updateUndoButton(count) {
  const btn = $('btn-undo');
  btn.textContent = '↶ (' + count + ')';
  btn.disabled = count === 0;
  btn.title = count > 0 ? 'Undo last move (' + count + ' available)' : 'Nothing to undo';
}

async function render() {
  const img = $('photo'); const ph = $('photo-placeholder'); const counter = $('counter'); const btnNN = $('btn-not-nude'); const btnN = $('btn-nude');
  const progressBar = $('progress-bar');
  
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

$('btn-fullscreen').onclick = toggleFullscreen;
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