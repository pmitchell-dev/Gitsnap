"""
Main entry point for the Gitsnap application.
Handles the tray icon, hotkeys, and overall application lifecycle.
"""
import os
import sys
import datetime
import threading
import tkinter as tk
from tkinter import simpledialog
from PIL import Image, ImageDraw, ImageTk
import pystray
from pynput import keyboard
from win11toast import toast

from capture import CaptureOverlay, set_dpi_awareness
from overlay import show_action_overlay
from upload import upload_image, generate_default_filename
from notify import copy_image_to_clipboard, copy_text_to_clipboard_and_notify
from settings import show_settings_window
from video import VideoRecorder
from config import load_config, DEBUG_LOG_FILE

# ── Kill any existing Gitsnap process before starting ────────────────────────
try:
    import psutil
    _current_pid = os.getpid()
    _exe_name = os.path.basename(sys.executable).lower()   # "gitsnap.exe" when frozen
    for _proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if _proc.pid == _current_pid:
                continue
            _pname = (_proc.info.get("name") or "").lower()
            _pexe  = os.path.basename(_proc.info.get("exe") or "").lower()
            if _pname == _exe_name or _pexe == _exe_name:
                _proc.terminate()
                try:
                    _proc.wait(timeout=2)
                except psutil.TimeoutExpired:
                    _proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
except (ImportError, AttributeError):
    pass  # psutil unavailable or not working — fall through silently

# Simple logging for debug
with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as _f_init:
    _f_init.write(f"\n[{datetime.datetime.now()}] Application starting...\n")

# Initial import check is no longer needed here as they are at top level
# If they fail, the app will crash with an ImportError which is fine for startup.


def _icon_path(filename):
    """Resolve an asset path that works both from source and frozen exe.
    PyInstaller single-file exes extract bundled datas to sys._MEIPASS."""
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, filename)


def create_tray_icon():
    """
    Creates the image for the tray icon.
    """
    try:
        img = Image.open(_icon_path("gitsnap_icon.png")).convert("RGBA")
        # Use robust resampling constant access
        resampling = getattr(Image, 'LANCZOS', getattr(Image, 'Resampling', None))
        if hasattr(resampling, 'LANCZOS'):
            resampling = resampling.LANCZOS
        img = img.resize((64, 64), resampling)
        return img
    except (IOError, OSError, tk.TclError, AttributeError):
        # Fallback: plain blue square
        image = Image.new('RGB', (64, 64), color=(30, 80, 160))
        d = ImageDraw.Draw(image)
        d.rectangle([16, 16, 48, 48], fill="white", outline="white")
        return image


def on_copy(img):
    """
    Handles copying an image or notification to the clipboard.
    """
    if isinstance(img, str) and img == "saved_video":
        toast("Video Saved", "The recording has been saved successfully.")
        return
    threading.Thread(target=copy_image_to_clipboard, args=(img,), daemon=True).start()


def on_upload(img, word=None, location_name=None, file_path=None, root=None):
    """
    Handles uploading an image to GitHub.
    """
    default_filename = generate_default_filename(file_path, word)
    
    custom_filename = simpledialog.askstring(
        "Upload to GitHub", 
        "Amend screenshot name before uploading:", 
        initialvalue=default_filename,
        parent=root
    )
    
    if custom_filename is None:
        return  # User cancelled
        
    if not custom_filename.strip():
        custom_filename = default_filename

    def process():
        link, error = upload_image(img, word, location_name, file_path, custom_filename)
        if link:
            copy_text_to_clipboard_and_notify(link)
        else:
            toast("Upload Failed", error or "Could not upload image.")
    threading.Thread(target=process, daemon=True).start()


class App:
    """
    Main application class for Gitsnap.
    """
    def __init__(self):
        """
        Initialize the application, setting up the root window and icons.
        """
        self.root = tk.Tk()
        self.root.withdraw()
        # Set icon for root AND all future Toplevel windows (Settings, etc.)
        try:
            _png = _icon_path("gitsnap_icon.png")
            with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as _f:
                _f.write(f"Icon path: {_png} exists={os.path.exists(_png)}\n")
            _pil = Image.open(_png).convert("RGBA")
            self._app_icon = ImageTk.PhotoImage(_pil)   # must stay referenced
            self.root.iconphoto(True, self._app_icon)   # True = apply to all Toplevels
        except (IOError, OSError, tk.TclError) as e:
            with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as _f:
                _f.write(f"Icon load error: {e}\n")
        self.root.bind("<<TriggerCapture>>", self.init_capture)
        self.root.bind("<<StopRecording>>", self.stop_recording)
        self.root.bind("<<OpenSettings>>", self.open_settings)
        self.icon = None
        self.current_word = None
        self.current_location = None
        self.current_type = "image"
        self.current_hotkey = None
        self.recorder = None
        self.active_hotkey = None
        self.current_overlay = None
        self.listener = None
        self.current_word_save = None
        self.current_location_save = None
        self.is_capturing = False
        self.hotkeys_paused = False

    def start(self):
        """
        Starts the application, tray icon, and hotkey listener.
        """
        try:
            with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as _f:
                _f.write("Initializing tray icon...\n")

            menu = pystray.Menu(
                pystray.MenuItem('Pause Hotkeys', self.toggle_pause, 
                                 checked=lambda item: self.hotkeys_paused),
                pystray.MenuItem('Settings', self.trigger_settings),
                pystray.MenuItem('Quit', self.quit)
            )
            self.icon = pystray.Icon("ScreenshotUtility", create_tray_icon(),
                                     "Screenshot Utility", menu)
            threading.Thread(target=self.icon.run, daemon=True).start()

            self.reload_hotkeys()

            with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as _f:
                _f.write("Hotkeys active. Starting Tkinter mainloop.\n")

            self.root.mainloop()
        except (tk.TclError, RuntimeError) as e:
            with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as _f:
                _f.write(f"Runtime Error in App.start: {e}\n")
            os._exit(1)

    def reload_hotkeys(self):
        """
        Reloads the hotkey listener with the current configuration.
        """
        if self.listener:
            self.listener.stop()

        config = load_config() or {}
        custom_hotkeys = config.get("CUSTOM_HOTKEYS", [])

        hotkeys_dict = {}

        for hk in custom_hotkeys:
            key = hk.get("key", "").strip()
            word = hk.get("word", "").strip() or None
            location = hk.get("location", "").strip() or None
            hk_type = hk.get("type", "image").strip()
            if key:
                hotkey_str = f'<alt>+{key}'
                hotkeys_dict[hotkey_str] = (
                    lambda w=word, loc=location, t=hk_type, k=hotkey_str:
                    self.on_hotkey(w, loc, t, k)
                )

        if not hotkeys_dict:
            hotkeys_dict['<alt>+s'] = lambda: self.on_hotkey(None, None, "image", "<alt>+s")

        hotkeys_dict['<esc>'] = self.cancel_capture

        self.listener = keyboard.GlobalHotKeys(hotkeys_dict)
        self.listener.start()

    def on_hotkey(self, word=None, location=None, hk_type="image", hotkey_str=None):
        """
        Callback for when a hotkey is pressed.
        """
        if self.hotkeys_paused:
            return

        if self.recorder and self.recorder.is_recording:
            if hotkey_str == self.active_hotkey:
                self.root.event_generate("<<StopRecording>>", when="tail")
            return

        if self.is_capturing:
            return

        self.current_word = word
        self.current_location = location
        self.current_type = hk_type
        self.current_hotkey = hotkey_str
        self.root.event_generate("<<TriggerCapture>>", when="tail")

    def toggle_pause(self, _icon, _item):
        """
        Toggles the hotkey pause state.
        """
        self.hotkeys_paused = not self.hotkeys_paused

    def trigger_settings(self, _icon, _item):
        """
        Triggers the settings window to open.
        """
        self.root.event_generate("<<OpenSettings>>", when="tail")

    def open_settings(self, _event):
        """
        Opens the settings window.
        """
        sw = show_settings_window(self.root)
        self.root.wait_window(sw.window)
        self.reload_hotkeys()

    def init_capture(self, _event):
        """
        Initializes the capture overlay.
        """
        if self.is_capturing:
            return
        self.is_capturing = True

        word = self.current_word
        location = self.current_location
        is_video = self.current_type == "video"
        
        upload_cb = lambda i, p=None: on_upload(i, word, location, file_path=p, root=self.root)

        def on_capture(img, x, y, bbox=None):
            if is_video and bbox:
                self.recorder = VideoRecorder(bbox)
                self.active_hotkey = self.current_hotkey
                self.current_word_save = word
                self.current_location_save = location
                self.recorder.start()
            else:
                # Screenshot path: FAB already dismissed the overlay before calling
                # on_capture — just fire on_copy with the pre-grabbed image.
                self.is_capturing = False
                on_copy(img)

        self.current_overlay = CaptureOverlay(
            self.root, on_capture, self.cancel_capture,
            is_video=is_video, on_upload=upload_cb
        )

    def cancel_capture(self, _event=None):
        """
        Cancels the current capture or recording.
        """
        if self.recorder and self.recorder.is_recording:
            self.recorder.stop()
            self.recorder = None
            toast("Recording Cancelled", "The video recording was cancelled.")
        
        if self.current_overlay:
            try:
                self.current_overlay.window.destroy()
            except tk.TclError:
                pass
            self.current_overlay = None
        
        self.is_capturing = False

    def stop_recording(self, _event):
        """
        Stops the current video recording.
        """
        if self.current_overlay:
            try:
                self.current_overlay.window.destroy()
            except tk.TclError:
                pass
            self.current_overlay = None
        self.is_capturing = False

        if self.recorder and self.recorder.is_recording:
            video_path = self.recorder.stop()
            self.recorder = None
            word = getattr(self, "current_word_save", None)
            location = getattr(self, "current_location_save", None)

            show_action_overlay(self.root, None, 0, 0, on_copy,
                                lambda i, p=None: on_upload(i, word, location, file_path=p, root=self.root),
                                is_video=True, video_path=video_path)

    def quit(self, icon, _item):
        """
        Quits the application.
        """
        icon.stop()
        self.root.quit()


if __name__ == "__main__":
    set_dpi_awareness()
    App().start()
