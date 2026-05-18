"""
Module for handling the screen capture overlay and selection.
"""
import ctypes
import tkinter as tk
from PIL import ImageGrab
import win32api
import win32con


def set_dpi_awareness():
    """
    Sets the process to be DPI aware to ensure correct screen coordinates on high-DPI displays.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


# FAB configuration constants
_FAB_RADIUS = 22
_FAB_GAP = 10
_FAB_OFFSET = 12       # distance from selection rect edge to first FAB centre
_FAB_FONT = ("Segoe UI Emoji", 13, "bold")
_FAB_LABEL_FONT = ("Segoe UI", 7, "bold")

_FABS = [
    {
        "icon": "⎘",
        "label": "COPY",
        "color": "#4f8ef7",
        "hover": "#78aaff",
        "tag": "fab_copy",
    },
    {
        "icon": "↑",
        "label": "UPLOAD",
        "color": "#7c5cbf",
        "hover": "#a07de0",
        "tag": "fab_upload",
    },
    {
        "icon": "✕",
        "label": "CANCEL",
        "color": "#555555",
        "hover": "#888888",
        "tag": "fab_cancel",
    },
]


class CaptureOverlay:
    """
    A full-screen transparent overlay for selecting an area to capture.
    After selection (screenshot mode) the overlay stays open and shows
    circular Floating Action Buttons (FABs) on the edge of the selected area.
    """

    def __init__(self, parent, on_capture, on_cancel, is_video=False, on_upload=None):
        self.parent = parent
        self.on_capture = on_capture
        self.on_cancel = on_cancel
        self.on_upload = on_upload
        self.is_video = is_video

        # Get virtual screen metrics
        self.v_left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
        self.v_top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
        self.v_width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
        self.v_height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)

        self.window = tk.Toplevel(self.parent)
        self.window.attributes('-alpha', 0.3)
        self.window.overrideredirect(True)
        self.window.geometry(
            f"{self.v_width}x{self.v_height}+{self.v_left}+{self.v_top}"
        )
        self.window.configure(background='black')
        self.window.attributes('-topmost', True)
        self.window.config(cursor="cross")

        self.canvas = tk.Canvas(
            self.window, cursor="cross", bg="black", highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)

        self.start_x = None
        self.start_y = None
        self.rect = None
        self.captured_img = None
        self._fab_items = []   # list of canvas item ids created for FABs

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.window.bind("<Escape>", lambda e: self._dismiss_cancel())

    # ------------------------------------------------------------------
    # Selection events
    # ------------------------------------------------------------------

    def show(self):
        """
        Placeholder for showing the overlay (currently unused as it shows on init).
        """

    def on_press(self, event):
        """
        Handles the mouse button press event to start selection.
        """
        self.start_x = self.canvas.canvasx(event.x)
        self.start_y = self.canvas.canvasy(event.y)
        color = 'green' if self.is_video else '#4f8ef7'
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline=color, width=2, fill='white'
        )

    def on_drag(self, event):
        """
        Handles the mouse drag event to update selection.
        """
        cur_x = self.canvas.canvasx(event.x)
        cur_y = self.canvas.canvasy(event.y)
        self.canvas.coords(self.rect, self.start_x, self.start_y, cur_x, cur_y)

    def on_release(self, event):
        """
        Handles the mouse button release event to finalise selection.
        For screenshot mode the overlay stays open and FABs are shown.
        For video mode, passive recording mode is entered as before.
        """
        end_x = self.canvas.canvasx(event.x)
        end_y = self.canvas.canvasy(event.y)
        x1 = min(self.start_x, end_x)
        y1 = min(self.start_y, end_y)
        x2 = max(self.start_x, end_x)
        y2 = max(self.start_y, end_y)

        if x2 - x1 <= 5 or y2 - y1 <= 5:
            # Too small — treat as cancel for video, ignore for screenshot
            if self.is_video:
                self._dismiss_cancel()
            return

        # Map selection coordinates to absolute virtual screen coordinates
        abs_x1 = int(x1) + self.v_left
        abs_y1 = int(y1) + self.v_top
        abs_x2 = int(x2) + self.v_left
        abs_y2 = int(y2) + self.v_top

        # Unbind selection so user can't re-draw while FABs are visible
        self.canvas.unbind("<ButtonPress-1>")
        self.canvas.unbind("<B1-Motion>")
        self.canvas.unbind("<ButtonRelease-1>")

        if self.is_video:
            self.enter_passive_mode()
            self.on_capture(None, abs_x2, abs_y2, bbox=(abs_x1, abs_y1, abs_x2, abs_y2))
        else:
            # Grab the screenshot immediately (overlay is transparent so pixels are correct)
            self.captured_img = ImageGrab.grab(
                bbox=(abs_x1, abs_y1, abs_x2, abs_y2), all_screens=True
            )
            # Polish the selection rect to indicate "ready"
            self.canvas.itemconfig(self.rect, outline='#4f8ef7', width=3)
            # Increase window opacity slightly so the selection is clearer
            self.window.attributes('-alpha', 0.45)
            # Show action FABs
            self.show_fabs(x1, y1, x2, y2)

    # ------------------------------------------------------------------
    # FAB rendering
    # ------------------------------------------------------------------

    def show_fabs(self, x1, y1, x2, y2):
        """
        Draws three circular Floating Action Buttons anchored to the
        bottom-right corner of the selection rectangle.
        The rightmost FAB's right edge aligns with x2; buttons grow
        leftward from that corner so none ever exceed the corner to the right.
        FABs sit just below y2, clamped above the rect if near the screen bottom.
        """
        r = _FAB_RADIUS
        gap = _FAB_GAP
        n = len(_FABS)

        # Vertical: sit _FAB_OFFSET px below the bottom edge
        cy = y2 + _FAB_OFFSET + r

        # If that goes off the bottom of the virtual screen, rise above the rect
        if cy + r > self.v_height:
            cy = y1 - _FAB_OFFSET - r
        # Safety clamp
        cy = max(r + 2, min(cy, self.v_height - r - 2))

        # Horizontal: rightmost FAB's right edge = x2.
        # Cancel is rightmost → its centre is at x2 - r.
        # Upload and Copy extend leftward from there.
        cx_rightmost = x2 - r
        # leftmost centre (Copy)
        cx_leftmost = cx_rightmost - (n - 1) * (2 * r + gap)
        # If leftmost goes off the left edge of the screen, shift right
        if cx_leftmost - r < 0:
            cx_leftmost = r + 2

        for i, fab in enumerate(_FABS):
            cx = cx_leftmost + i * (2 * r + gap)
            self._draw_fab(cx, cy, r, fab, x1, y1, x2, y2)

    def _draw_fab(self, cx, cy, r, fab_def, x1, y1, x2, y2):
        """
        Draws a single circular FAB on the canvas and wires up its events.
        """
        tag = fab_def["tag"]
        color = fab_def["color"]
        hover = fab_def["hover"]
        icon = fab_def["icon"]
        label = fab_def["label"]

        # Shadow circle (subtle depth)
        shadow_id = self.canvas.create_oval(
            cx - r + 3, cy - r + 3, cx + r + 3, cy + r + 3,
            fill="#000000", outline="", tags=(tag, "fab_shadow")
        )
        self.canvas.itemconfig(shadow_id, stipple="gray25")

        # Main circle
        circle_id = self.canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=color, outline="#ffffff", width=2, tags=(tag, "fab_circle")
        )

        # Icon
        icon_id = self.canvas.create_text(
            cx, cy - 3,
            text=icon, fill="white",
            font=_FAB_FONT, tags=(tag, "fab_icon")
        )

        # Label beneath icon
        label_id = self.canvas.create_text(
            cx, cy + 10,
            text=label, fill="white",
            font=_FAB_LABEL_FONT, tags=(tag, "fab_label")
        )

        item_ids = [shadow_id, circle_id, icon_id, label_id]
        self._fab_items.extend(item_ids)

        # Hover: lighten circle
        def on_enter(_event, cid=circle_id, h=hover, curs="hand2"):
            self.canvas.itemconfig(cid, fill=h)
            self.window.config(cursor=curs)

        def on_leave(_event, cid=circle_id, c=color):
            self.canvas.itemconfig(cid, fill=c)
            self.window.config(cursor="arrow")

        # Click handlers per FAB type
        if label == "COPY":
            def on_click(_event, img=None):
                img = self.captured_img
                self._dismiss_action()
                self.on_capture(img, 0, 0, bbox=None)
        elif label == "UPLOAD":
            def on_click(_event):
                img = self.captured_img
                self._dismiss_action()
                if self.on_upload:
                    self.on_upload(img, None)
        else:  # CANCEL
            def on_click(_event):
                self._dismiss_cancel()

        for item_id in (circle_id, icon_id, label_id):
            self.canvas.tag_bind(item_id, "<Enter>", on_enter)
            self.canvas.tag_bind(item_id, "<Leave>", on_leave)
            self.canvas.tag_bind(item_id, "<Button-1>", on_click)

    def _dismiss_action(self):
        """
        Closes the overlay after a Copy or Upload action is chosen.
        """
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    def _dismiss_cancel(self):
        """
        Closes the overlay and fires the on_cancel callback.
        """
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        self.on_cancel()

    # ------------------------------------------------------------------
    # Video passive mode (unchanged)
    # ------------------------------------------------------------------

    def enter_passive_mode(self):
        """
        Enters passive mode during recording, making the overlay click-through.
        """
        self.window.attributes('-transparentcolor', 'white')
        hwnd = self.window.winfo_id()
        # GWL_EXSTYLE = -20, WS_EX_TRANSPARENT = 0x20
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x20)
        self.canvas.unbind("<ButtonPress-1>")
        self.canvas.unbind("<B1-Motion>")
        self.canvas.unbind("<ButtonRelease-1>")
        # Thicker border during recording
        self.canvas.itemconfig(self.rect, width=3)
