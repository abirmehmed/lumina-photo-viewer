#!/usr/bin/env python3
"""
Lumina — Official Grade Photo Viewer for Linux
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Supports: JPEG, JPG, PNG, WebP, BMP, TIFF, GIF, SVG
Features:
  • Two-finger pinch-to-zoom (touchpad) — smooth & gentle
  • Ctrl+wheel zoom
  • Click-and-drag pan (when zoomed)
  • Smooth kinetic scrolling
  • Visual crop with rule-of-thirds overlay
  • Rotate, flip, brightness/contrast
  • Set as desktop wallpaper
  • Folder navigation (←/→ keys)
  • Drag & drop images
"""

import sys
import os
import gi
import pathlib
import mimetypes
from datetime import datetime

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')

from gi.repository import Gtk, Adw, Gdk, GdkPixbuf, GLib, Gio, GObject, cairo


class SmoothImageCanvas(Gtk.DrawingArea):
    """
    High-performance image canvas with gentle two-finger pinch zoom.
    """

    def __init__(self):
        super().__init__()
        self.pixbuf = None
        self.original_pixbuf = None

        # Zoom state
        self.zoom_level = 1.0
        self.target_zoom = 1.0
        self.zoom_animation_id = None

        # Pinch tracking
        self.pinch_base_zoom = 1.0      # zoom when pinch started
        self.pinch_last_scale = 1.0     # last reported scale

        # Pan state
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.target_offset_x = 0.0
        self.target_offset_y = 0.0
        self.pan_animation_id = None

        # Physics
        self.velocity_x = 0.0
        self.velocity_y = 0.0
        self.friction = 0.92
        self.is_panning = False
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.last_pan_x = 0
        self.last_pan_y = 0
        self.last_pan_time = 0

        # Transformations
        self.rotation = 0
        self.flip_h = False
        self.flip_v = False
        self.brightness = 1.0
        self.contrast = 1.0

        # ── Gesture Controllers ──

        # 1. Two-finger PINCH ZOOM (touchpad) — GENTLE sensitivity
        self.zoom_gesture = Gtk.GestureZoom.new()
        self.zoom_gesture.connect('scale-changed', self.on_pinch_scale_changed)
        self.zoom_gesture.connect('begin', self.on_pinch_begin)
        self.zoom_gesture.connect('end', self.on_pinch_end)
        self.add_controller(self.zoom_gesture)

        # 2. Click-drag PAN
        self.drag_gesture = Gtk.GestureDrag.new()
        self.drag_gesture.connect('drag-begin', self.on_drag_begin)
        self.drag_gesture.connect('drag-update', self.on_drag_update)
        self.drag_gesture.connect('drag-end', self.on_drag_end)
        self.add_controller(self.drag_gesture)

        # 3. Scroll wheel (Ctrl+scroll = zoom, scroll = pan when zoomed)
        self.scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.HORIZONTAL |
            Gtk.EventControllerScrollFlags.DISCRETE
        )
        self.scroll_controller.connect('scroll', self.on_scroll)
        self.add_controller(self.scroll_controller)

        # 4. Drag & drop files
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect('drop', self.on_drop)
        self.add_controller(drop)

        self.set_draw_func(self.draw)

    # ═══════════════════════════════════════════════════════
    #  TWO-FINGER PINCH ZOOM — GENTLE & SMOOTH
    # ═══════════════════════════════════════════════════════

    def on_pinch_begin(self, gesture, sequence):
        """Pinch started — remember where we started"""
        self.stop_zoom_animation()
        self.stop_pan_animation()
        self.is_panning = True
        self.pinch_base_zoom = self.zoom_level
        self.pinch_last_scale = 1.0

    def on_pinch_scale_changed(self, gesture, scale):
        """
        Gentle pinch zoom:
        - scale is cumulative from gesture start (1.0 = no change)
        - We dampen the effect: use scale^0.4 for gentle response
        - Clamp to reasonable bounds
        """
        # Dampen the pinch: raising to 0.4 makes it much gentler
        # scale=2.0 (double finger spread) → 2^0.4 ≈ 1.32x zoom (was 2x!)
        # scale=0.5 (pinch halfway) → 0.5^0.4 ≈ 0.76x zoom (was 0.5x!)
        dampened = pow(scale, 0.4)
        new_zoom = self.pinch_base_zoom * dampened
        new_zoom = max(0.1, min(new_zoom, 20.0))

        self.target_zoom = new_zoom
        self.zoom_level = new_zoom
        self.clamp_offset()
        self.queue_draw()
        self.emit_zoom_changed()

    def on_pinch_end(self, gesture, sequence):
        """Pinch ended — snap to fit if very close"""
        self.is_panning = False
        if 0.85 < self.zoom_level < 1.15:
            self.animate_zoom_to(1.0)
            self.animate_pan_to(0, 0)

    # ═══════════════════════════════════════════════════════
    #  MOUSE WHEEL ZOOM
    # ═══════════════════════════════════════════════════════

    def on_scroll(self, controller, dx, dy):
        state = controller.get_current_event_state()

        if state & Gdk.ModifierType.CONTROL_MASK:
            # Ctrl+Scroll = Zoom (gentler steps)
            self.stop_zoom_animation()
            if dy > 0:
                self.target_zoom = max(self.zoom_level / 1.12, 0.1)
            else:
                self.target_zoom = min(self.zoom_level * 1.12, 20.0)
            self.animate_zoom_to(self.target_zoom)
            return True
        else:
            # Normal scroll = Pan when zoomed
            if self.zoom_level > 1.0:
                self.stop_pan_animation()
                self.offset_x -= dx * 30
                self.offset_y -= dy * 30
                self.clamp_offset()
                self.queue_draw()
                return True
        return False

    # ═══════════════════════════════════════════════════════
    #  CLICK-DRAG PAN
    # ═══════════════════════════════════════════════════════

    def on_drag_begin(self, gesture, start_x, start_y):
        if self.zoom_level > 1.0 or self.rotation != 0:
            self.is_panning = True
            self.stop_pan_animation()
            self.drag_start_x = start_x
            self.drag_start_y = start_y
            self.last_pan_x = start_x
            self.last_pan_y = start_y
            self.last_pan_time = GLib.get_monotonic_time()
            self.velocity_x = 0
            self.velocity_y = 0
            self.set_cursor_from_name("grabbing")

    def on_drag_update(self, gesture, offset_x, offset_y):
        if self.is_panning:
            now = GLib.get_monotonic_time()
            dt = (now - self.last_pan_time) / 1000000.0
            if dt > 0:
                self.velocity_x = (offset_x - (self.last_pan_x - self.drag_start_x)) / dt
                self.velocity_y = (offset_y - (self.last_pan_y - self.drag_start_y)) / dt

            self.offset_x += offset_x - (self.last_pan_x - self.drag_start_x)
            self.offset_y += offset_y - (self.last_pan_y - self.drag_start_y)
            self.last_pan_x = self.drag_start_x + offset_x
            self.last_pan_y = self.drag_start_y + offset_y
            self.last_pan_time = now
            self.clamp_offset()
            self.queue_draw()

    def on_drag_end(self, gesture, offset_x, offset_y):
        self.is_panning = False
        self.set_cursor_from_name(None)
        if abs(self.velocity_x) > 50 or abs(self.velocity_y) > 50:
            self.start_kinetic_scroll()

    # ═══════════════════════════════════════════════════════
    #  KINETIC / ANIMATED SCROLLING
    # ═══════════════════════════════════════════════════════

    def start_kinetic_scroll(self):
        def tick():
            if abs(self.velocity_x) < 10 and abs(self.velocity_y) < 10:
                self.pan_animation_id = None
                return False
            self.offset_x += self.velocity_x * 0.016
            self.offset_y += self.velocity_y * 0.016
            self.velocity_x *= self.friction
            self.velocity_y *= self.friction
            self.clamp_offset()
            self.queue_draw()
            return True

        if self.pan_animation_id:
            GLib.source_remove(self.pan_animation_id)
        self.pan_animation_id = GLib.timeout_add(16, tick)

    def stop_pan_animation(self):
        if self.pan_animation_id:
            GLib.source_remove(self.pan_animation_id)
            self.pan_animation_id = None

    def animate_pan_to(self, tx, ty):
        self.stop_pan_animation()
        self.target_offset_x = tx
        self.target_offset_y = ty

        def tick():
            dx = self.target_offset_x - self.offset_x
            dy = self.target_offset_y - self.offset_y
            if abs(dx) < 0.5 and abs(dy) < 0.5:
                self.offset_x = self.target_offset_x
                self.offset_y = self.target_offset_y
                self.pan_animation_id = None
                self.queue_draw()
                return False
            self.offset_x += dx * 0.15
            self.offset_y += dy * 0.15
            self.queue_draw()
            return True

        self.pan_animation_id = GLib.timeout_add(16, tick)

    # ═══════════════════════════════════════════════════════
    #  ANIMATED ZOOM
    # ═══════════════════════════════════════════════════════

    def animate_zoom_to(self, target):
        self.stop_zoom_animation()
        self.target_zoom = target

        def tick():
            diff = self.target_zoom - self.zoom_level
            if abs(diff) < 0.005:
                self.zoom_level = self.target_zoom
                self.zoom_animation_id = None
                self.clamp_offset()
                self.queue_draw()
                self.emit_zoom_changed()
                return False
            self.zoom_level += diff * 0.15
            self.clamp_offset()
            self.queue_draw()
            self.emit_zoom_changed()
            return True

        self.zoom_animation_id = GLib.timeout_add(16, tick)

    def stop_zoom_animation(self):
        if self.zoom_animation_id:
            GLib.source_remove(self.zoom_animation_id)
            self.zoom_animation_id = None

    # ═══════════════════════════════════════════════════════
    #  IMAGE LOADING & TRANSFORMS
    # ═══════════════════════════════════════════════════════

    def set_image(self, pixbuf):
        self.original_pixbuf = pixbuf
        self.pixbuf = pixbuf
        self.zoom_level = 1.0
        self.target_zoom = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self.rotation = 0
        self.flip_h = False
        self.flip_v = False
        self.brightness = 1.0
        self.contrast = 1.0
        self.apply_transformations()
        self.emit_zoom_changed()

    def apply_transformations(self):
        if self.original_pixbuf is None:
            return

        pixbuf = self.original_pixbuf.copy()

        if self.rotation == 90:
            pixbuf = pixbuf.rotate_simple(GdkPixbuf.PixbufRotation.CLOCKWISE)
        elif self.rotation == 180:
            pixbuf = pixbuf.rotate_simple(GdkPixbuf.PixbufRotation.UPSIDEDOWN)
        elif self.rotation == 270:
            pixbuf = pixbuf.rotate_simple(GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE)

        if self.flip_h:
            pixbuf = pixbuf.flip(True)
        if self.flip_v:
            pixbuf = pixbuf.flip(False)

        if self.brightness != 1.0 or self.contrast != 1.0:
            pixbuf = self.apply_brightness_contrast(pixbuf)

        self.pixbuf = pixbuf
        self.queue_draw()

    def apply_brightness_contrast(self, pixbuf):
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        new_pixbuf = pixbuf.copy()
        pixels = new_pixbuf.get_pixels()
        n_channels = new_pixbuf.get_n_channels()
        rowstride = new_pixbuf.get_rowstride()

        for y in range(height):
            for x in range(width):
                offset = y * rowstride + x * n_channels
                for c in range(min(3, n_channels)):
                    val = pixels[offset + c]
                    val = int((val - 128) * self.contrast + 128)
                    val = int(val * self.brightness)
                    pixels[offset + c] = max(0, min(255, val))
        return new_pixbuf

    # ═══════════════════════════════════════════════════════
    #  ZOOM API
    # ═══════════════════════════════════════════════════════

    def zoom_in(self):
        self.animate_zoom_to(min(self.zoom_level * 1.3, 20.0))

    def zoom_out(self):
        self.animate_zoom_to(max(self.zoom_level / 1.3, 0.1))

    def zoom_fit(self):
        self.animate_zoom_to(1.0)
        self.animate_pan_to(0, 0)

    def get_fit_zoom(self):
        if self.pixbuf is None:
            return 1.0
        alloc = self.get_allocation()
        if alloc.width == 0 or alloc.height == 0:
            return 1.0
        img_w = self.pixbuf.get_width()
        img_h = self.pixbuf.get_height()
        return min(alloc.width / img_w, alloc.height / img_h, 1.0)

    # ═══════════════════════════════════════════════════════
    #  ROTATE / FLIP
    # ═══════════════════════════════════════════════════════

    def rotate_left(self):
        self.rotation = (self.rotation - 90) % 360
        self.apply_transformations()

    def rotate_right(self):
        self.rotation = (self.rotation + 90) % 360
        self.apply_transformations()

    def toggle_flip_h(self):
        self.flip_h = not self.flip_h
        self.apply_transformations()

    def toggle_flip_v(self):
        self.flip_v = not self.flip_v
        self.apply_transformations()

    def set_brightness(self, value):
        self.brightness = value
        self.apply_transformations()

    def set_contrast(self, value):
        self.contrast = value
        self.apply_transformations()

    def get_current_pixbuf(self):
        return self.pixbuf

    # ═══════════════════════════════════════════════════════
    #  BOUNDING / CLAMPING
    # ═══════════════════════════════════════════════════════

    def clamp_offset(self):
        if self.pixbuf is None or self.zoom_level <= 1.0:
            self.offset_x = 0
            self.offset_y = 0
            return

        alloc = self.get_allocation()
        img_w = self.pixbuf.get_width() * self.zoom_level
        img_h = self.pixbuf.get_height() * self.zoom_level

        max_x = max(0, (img_w - alloc.width) / 2)
        max_y = max(0, (img_h - alloc.height) / 2)

        self.offset_x = max(-max_x, min(max_x, self.offset_x))
        self.offset_y = max(-max_y, min(max_y, self.offset_y))

    # ═══════════════════════════════════════════════════════
    #  DRAWING
    # ═══════════════════════════════════════════════════════

    def draw(self, area, cr, width, height):
        if self.pixbuf is None:
            cr.set_source_rgb(0.12, 0.12, 0.14)
            cr.paint()
            return

        img_w = self.pixbuf.get_width()
        img_h = self.pixbuf.get_height()

        fit_zoom = min(width / img_w, height / img_h, 1.0)
        actual_zoom = fit_zoom * self.zoom_level

        scaled_w = img_w * actual_zoom
        scaled_h = img_h * actual_zoom

        x = (width - scaled_w) / 2 + self.offset_x
        y = (height - scaled_h) / 2 + self.offset_y

        if self.pixbuf.get_has_alpha():
            self.draw_checkerboard(cr, x, y, scaled_w, scaled_h)

        cr.save()
        cr.translate(x, y)
        cr.scale(actual_zoom, actual_zoom)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint()
        cr.restore()

    def draw_checkerboard(self, cr, x, y, w, h):
        size = 12
        cr.save()
        cr.rectangle(x, y, w, h)
        cr.clip()
        cols = int(w / size) + 2
        rows = int(h / size) + 2
        for row in range(rows):
            for col in range(cols):
                if (row + col) % 2 == 0:
                    cr.set_source_rgb(0.94, 0.94, 0.94)
                else:
                    cr.set_source_rgb(0.86, 0.86, 0.86)
                cr.rectangle(x + col * size, y + row * size, size, size)
                cr.fill()
        cr.restore()

    # ═══════════════════════════════════════════════════════
    #  DRAG & DROP
    # ═══════════════════════════════════════════════════════

    def on_drop(self, drop, value, x, y):
        files = value.get_files()
        if files:
            self.get_root().open_file(files[0].get_path())
        return True

    # ═══════════════════════════════════════════════════════
    #  SIGNALS
    # ═══════════════════════════════════════════════════════

    def emit_zoom_changed(self):
        self.emit('zoom-changed', self.zoom_level)


GObject.signal_new('zoom-changed', SmoothImageCanvas,
                   GObject.SignalFlags.RUN_LAST, GObject.TYPE_NONE,
                   (GObject.TYPE_DOUBLE,))


class CropOverlay(Gtk.Overlay):
    """Visual crop selection with rule-of-thirds guides"""

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self.crop_mode = False
        self.start_x = self.start_y = 0
        self.end_x = self.end_y = 0
        self.is_selecting = False

        self.overlay = Gtk.DrawingArea()
        self.overlay.set_draw_func(self.draw_crop_overlay)
        self.overlay.set_visible(False)

        gesture = Gtk.GestureDrag.new()
        gesture.connect('drag-begin', self.on_crop_begin)
        gesture.connect('drag-update', self.on_crop_update)
        gesture.connect('drag-end', self.on_crop_end)
        self.overlay.add_controller(gesture)

        self.add_overlay(self.overlay)

    def set_crop_mode(self, active):
        self.crop_mode = active
        self.overlay.set_visible(active)
        if not active:
            self.is_selecting = False
            self.queue_draw()

    def on_crop_begin(self, gesture, x, y):
        if self.crop_mode:
            self.is_selecting = True
            self.start_x = x
            self.start_y = y
            self.end_x = x
            self.end_y = y

    def on_crop_update(self, gesture, offset_x, offset_y):
        if self.is_selecting:
            self.end_x = self.start_x + offset_x
            self.end_y = self.start_y + offset_y
            self.overlay.queue_draw()

    def on_crop_end(self, gesture, offset_x, offset_y):
        self.is_selecting = False

    def get_crop_rect(self):
        if self.canvas.pixbuf is None:
            return None

        x1, x2 = min(self.start_x, self.end_x), max(self.start_x, self.end_x)
        y1, y2 = min(self.start_y, self.end_y), max(self.start_y, self.end_y)

        if x2 - x1 < 10 or y2 - y1 < 10:
            return None

        img_w = self.canvas.pixbuf.get_width()
        img_h = self.canvas.pixbuf.get_height()
        alloc = self.canvas.get_allocation()
        widget_w, widget_h = alloc.width, alloc.height

        fit_zoom = min(widget_w / img_w, widget_h / img_h, 1.0)
        actual_zoom = fit_zoom * self.canvas.zoom_level

        scaled_w = img_w * actual_zoom
        scaled_h = img_h * actual_zoom
        offset_x = (widget_w - scaled_w) / 2 + self.canvas.offset_x
        offset_y = (widget_h - scaled_h) / 2 + self.canvas.offset_y

        ix1 = int((x1 - offset_x) / actual_zoom)
        iy1 = int((y1 - offset_y) / actual_zoom)
        ix2 = int((x2 - offset_x) / actual_zoom)
        iy2 = int((y2 - offset_y) / actual_zoom)

        ix1 = max(0, min(ix1, img_w))
        iy1 = max(0, min(iy1, img_h))
        ix2 = max(0, min(ix2, img_w))
        iy2 = max(0, min(iy2, img_h))

        return (ix1, iy1, ix2 - ix1, iy2 - iy1)

    def draw_crop_overlay(self, area, cr, width, height):
        if not self.is_selecting:
            return

        x1, x2 = min(self.start_x, self.end_x), max(self.start_x, self.end_x)
        y1, y2 = min(self.start_y, self.end_y), max(self.start_y, self.end_y)
        w, h = x2 - x1, y2 - y1

        # Darken outside
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.rectangle(0, 0, width, height)
        cr.rectangle(x1, y1, w, h)
        cr.set_fill_rule(cairo.FillRule.EVEN_ODD)
        cr.fill()

        # White border
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.set_line_width(2)
        cr.rectangle(x1, y1, w, h)
        cr.stroke()

        # Rule of thirds
        cr.set_source_rgba(1, 1, 1, 0.4)
        cr.set_line_width(1)
        for i in (1, 2):
            cr.move_to(x1 + w * i / 3, y1)
            cr.line_to(x1 + w * i / 3, y2)
            cr.move_to(x1, y1 + h * i / 3)
            cr.line_to(x2, y1 + h * i / 3)
        cr.stroke()

        # Corner handles
        handle = 8
        cr.set_source_rgba(0.2, 0.6, 1, 0.9)
        for cx, cy in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
            cr.rectangle(cx - handle / 2, cy - handle / 2, handle, handle)
            cr.fill()


class LuminaWindow(Adw.ApplicationWindow):
    """Main window — clean toolbar, no duplicates"""

    def __init__(self, app, file_path=None):
        super().__init__(application=app)
        self.set_title("Lumina")
        self.set_default_size(1280, 840)

        self.current_file = None
        self.current_folder = None
        self.file_list = []
        self.current_index = -1

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_box)

        self.build_header()
        self.build_toolbar()
        self.build_image_area()
        self.build_statusbar()
        self.setup_shortcuts()

        if file_path:
            GLib.idle_add(self.open_file, file_path)

    def build_header(self):
        self.header = Adw.HeaderBar()
        self.header.set_show_end_title_buttons(True)
        self.main_box.append(self.header)

    def build_toolbar(self):
        """Clean toolbar — each button appears exactly once"""
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        toolbar.set_margin_start(10)
        toolbar.set_margin_end(10)
        toolbar.set_margin_top(4)
        toolbar.set_margin_bottom(4)

        def btn(icon, tip, callback):
            b = Gtk.Button(icon_name=icon)
            b.set_tooltip_text(tip)
            b.connect('clicked', callback)
            return b

        # ── Group 1: File ──
        toolbar.append(btn("document-open-symbolic", "Open Image (Ctrl+O)", self.on_open_clicked))
        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # ── Group 2: Navigation ──
        toolbar.append(btn("go-previous-symbolic", "Previous Image (←)", self.on_prev_clicked))
        toolbar.append(btn("go-next-symbolic", "Next Image (→)", self.on_next_clicked))
        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # ── Group 3: Zoom ──
        toolbar.append(btn("zoom-out-symbolic", "Zoom Out (Ctrl+-)", lambda b: self.canvas.zoom_out()))
        toolbar.append(btn("zoom-fit-best-symbolic", "Fit to Window (Ctrl+0)", lambda b: self.canvas.zoom_fit()))
        toolbar.append(btn("zoom-in-symbolic", "Zoom In (Ctrl++)", lambda b: self.canvas.zoom_in()))
        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # ── Group 4: Transform ──
        toolbar.append(btn("object-rotate-left-symbolic", "Rotate Left (Ctrl+L)", lambda b: self.canvas.rotate_left()))
        toolbar.append(btn("object-rotate-right-symbolic", "Rotate Right (Ctrl+R)", lambda b: self.canvas.rotate_right()))
        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # ── Group 5: Edit ──
        self.crop_btn = Gtk.ToggleButton(icon_name="image-crop-symbolic")
        self.crop_btn.set_tooltip_text("Crop Tool (Ctrl+Shift+C)")
        self.crop_btn.connect('toggled', self.on_crop_toggled)
        toolbar.append(self.crop_btn)

        edit_btn = Gtk.MenuButton(icon_name="document-edit-symbolic")
        edit_btn.set_tooltip_text("Edit Tools")
        menu = Gio.Menu()
        menu.append("Flip Horizontal", "app.flip_h")
        menu.append("Flip Vertical", "app.flip_v")
        menu.append("Brightness / Contrast...", "app.brightness_contrast")
        menu.append("Set as Wallpaper", "app.set_wallpaper")
        edit_btn.set_menu_model(menu)
        toolbar.append(edit_btn)

        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # ── Group 6: Save (ONLY ONE!) ──
        toolbar.append(btn("document-save-symbolic", "Save As (Ctrl+Shift+S)", self.on_save_clicked))

        # ── Spacer + Info ──
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        toolbar.append(spacer)

        toolbar.append(btn("dialog-information-symbolic", "Image Info (I)", self.on_info_clicked))

        self.main_box.append(toolbar)

    def build_image_area(self):
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.scrolled.set_hexpand(True)

        self.canvas = SmoothImageCanvas()
        self.canvas.connect('zoom-changed', self.on_zoom_changed)

        self.crop_overlay = CropOverlay(self.canvas)
        self.crop_overlay.set_child(self.canvas)

        self.scrolled.set_child(self.crop_overlay)
        self.main_box.append(self.scrolled)

    def build_statusbar(self):
        self.statusbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.statusbar.set_margin_start(14)
        self.statusbar.set_margin_end(14)
        self.statusbar.set_margin_top(6)
        self.statusbar.set_margin_bottom(8)

        self.filename_label = Gtk.Label(label="Ready")
        self.filename_label.set_xalign(0)
        self.statusbar.append(self.filename_label)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self.statusbar.append(spacer)

        self.zoom_label = Gtk.Label(label="100%")
        self.statusbar.append(self.zoom_label)

        self.res_label = Gtk.Label(label="")
        self.statusbar.append(self.res_label)

        self.main_box.append(self.statusbar)

    def on_zoom_changed(self, canvas, zoom):
        self.zoom_label.set_text(f"{int(zoom * 100)}%")

    def setup_shortcuts(self):
        controller = Gtk.EventControllerKey.new()
        controller.connect('key-pressed', self.on_key_pressed)
        self.add_controller(controller)

    def on_key_pressed(self, controller, keyval, keycode, state):
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK

        if ctrl:
            if keyval == Gdk.KEY_o:
                self.on_open_clicked(None)
                return True
            elif keyval in (Gdk.KEY_plus, Gdk.KEY_equal):
                self.canvas.zoom_in()
                return True
            elif keyval == Gdk.KEY_minus:
                self.canvas.zoom_out()
                return True
            elif keyval == Gdk.KEY_0:
                self.canvas.zoom_fit()
                return True
            elif keyval == Gdk.KEY_l:
                self.canvas.rotate_left()
                return True
            elif keyval == Gdk.KEY_r:
                self.canvas.rotate_right()
                return True
            elif keyval == Gdk.KEY_s and shift:
                self.on_save_clicked(None)
                return True
            elif keyval == Gdk.KEY_c and shift:
                self.crop_btn.set_active(not self.crop_btn.get_active())
                return True
        else:
            if keyval == Gdk.KEY_Left:
                self.on_prev_clicked(None)
                return True
            elif keyval == Gdk.KEY_Right:
                self.on_next_clicked(None)
                return True
            elif keyval == Gdk.KEY_i:
                self.on_info_clicked(None)
                return True
            elif keyval == Gdk.KEY_Escape:
                if self.crop_btn.get_active():
                    self.crop_btn.set_active(False)
                return True
        return False

    def on_open_clicked(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title("Open Image")
        f = Gtk.FileFilter()
        f.set_name("Images")
        for p in ['*.jpg', '*.jpeg', '*.png', '*.webp', '*.bmp', '*.tiff', '*.gif', '*.svg']:
            f.add_pattern(p)
        dialog.set_filters(Gio.ListStore.new(Gtk.FileFilter))
        dialog.get_filters().append(f)
        dialog.open(self, None, self.on_open_response)

    def on_open_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                self.open_file(file.get_path())
        except Exception as e:
            self.show_error(f"Open failed: {e}")

    def open_file(self, file_path):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(file_path)
            self.canvas.set_image(pixbuf)
            self.current_file = file_path
            self.current_folder = os.path.dirname(file_path)
            self.refresh_file_list()
            self.current_index = self.file_list.index(file_path) if file_path in self.file_list else -1

            filename = os.path.basename(file_path)
            self.set_title(f"{filename} — Lumina")
            self.filename_label.set_text(filename)
            self.res_label.set_text(f"{pixbuf.get_width()}×{pixbuf.get_height()}")
        except Exception as e:
            self.show_error(f"Could not open image: {e}")

    def refresh_file_list(self):
        if not self.current_folder:
            return
        exts = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.gif', '.svg'}
        self.file_list = []
        try:
            for f in sorted(os.listdir(self.current_folder)):
                if os.path.splitext(f.lower())[1] in exts:
                    self.file_list.append(os.path.join(self.current_folder, f))
        except:
            pass

    def on_prev_clicked(self, button):
        if self.current_index > 0:
            self.current_index -= 1
            self.open_file(self.file_list[self.current_index])

    def on_next_clicked(self, button):
        if self.current_index < len(self.file_list) - 1:
            self.current_index += 1
            self.open_file(self.file_list[self.current_index])

    def on_crop_toggled(self, button):
        active = button.get_active()
        self.crop_overlay.set_crop_mode(active)
        if not active:
            rect = self.crop_overlay.get_crop_rect()
            if rect:
                self.apply_crop(rect)

    def apply_crop(self, rect):
        if self.canvas.pixbuf is None:
            return
        x, y, w, h = rect
        try:
            cropped = self.canvas.pixbuf.new_subpixbuf(x, y, w, h)
            self.canvas.set_image(cropped)
            self.res_label.set_text(f"{cropped.get_width()}×{cropped.get_height()}")
        except Exception as e:
            self.show_error(f"Crop failed: {e}")

    def on_save_clicked(self, button):
        if self.canvas.pixbuf is None:
            return
        dialog = Gtk.FileDialog()
        dialog.set_title("Save Image")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        for name, pat in [("PNG", "*.png"), ("JPEG", "*.jpg"), ("WebP", "*.webp")]:
            f = Gtk.FileFilter()
            f.set_name(name)
            f.add_pattern(pat)
            filters.append(f)
        dialog.set_filters(filters)
        dialog.save(self, None, self.on_save_response)

    def on_save_response(self, dialog, result):
        try:
            file = dialog.save_finish(result)
            if file:
                path = file.get_path()
                pixbuf = self.canvas.get_current_pixbuf()
                opts = []
                vals = []
                if path.lower().endswith(('.jpg', '.jpeg')):
                    fmt, opts, vals = "jpeg", ["quality"], ["95"]
                elif path.lower().endswith('.webp'):
                    fmt = "webp"
                else:
                    if not path.lower().endswith('.png'):
                        path += '.png'
                    fmt = "png"
                pixbuf.savev(path, fmt, opts, vals)
        except GLib.Error as e:
            if e.code != 2:
                self.show_error(f"Save failed: {e.message}")
        except Exception as e:
            self.show_error(f"Save failed: {e}")

    def on_info_clicked(self, button):
        if not self.current_file:
            return
        dialog = Adw.MessageDialog.new(self, "Image Information")
        info = [
            f"<b>Filename:</b> {os.path.basename(self.current_file)}",
            f"<b>Path:</b> {self.current_file}",
        ]
        if self.canvas.pixbuf:
            info.append(f"<b>Dimensions:</b> {self.canvas.pixbuf.get_width()}×{self.canvas.pixbuf.get_height()}")
            info.append(f"<b>Channels:</b> {self.canvas.pixbuf.get_n_channels()}")
            info.append(f"<b>Alpha:</b> {'Yes' if self.canvas.pixbuf.get_has_alpha() else 'No'}")
        try:
            size = os.path.getsize(self.current_file)
            info.append(f"<b>Size:</b> {size / 1024:.1f} KB")
            mtime = os.path.getmtime(self.current_file)
            info.append(f"<b>Modified:</b> {datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')}")
        except:
            pass
        dialog.set_body("\n".join(info))
        dialog.add_response("ok", "OK")
        dialog.present()

    def show_brightness_contrast_dialog(self):
        dialog = Adw.MessageDialog.new(self, "Brightness & Contrast")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        def make_row(label_text, value, callback):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.append(Gtk.Label(label=label_text))
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.1, 3.0, 0.05)
            scale.set_value(value)
            scale.set_hexpand(True)
            scale.connect('value-changed', lambda s: callback(s.get_value()))
            row.append(scale)
            return row

        box.append(make_row("Brightness:", self.canvas.brightness, self.canvas.set_brightness))
        box.append(make_row("Contrast:", self.canvas.contrast, self.canvas.set_contrast))
        dialog.set_extra_child(box)
        dialog.add_response("reset", "Reset")
        dialog.add_response("ok", "OK")
        dialog.connect('response', lambda d, r: (self.canvas.set_brightness(1.0), self.canvas.set_contrast(1.0)) if r == "reset" else None)
        dialog.present()

    def set_wallpaper(self):
        if not self.current_file:
            return
        import subprocess
        abs_path = os.path.abspath(self.current_file)
        cmds = [
            ["gsettings", "set", "org.gnome.desktop.background", "picture-uri", f"file://{abs_path}"],
            ["gsettings", "set", "org.gnome.desktop.background", "picture-uri-dark", f"file://{abs_path}"],
            ["xfconf-query", "-c", "xfce4-desktop", "-p", "/backdrop/screen0/monitor0/workspace0/last-image", "-s", abs_path],
        ]
        for cmd in cmds:
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                self.show_toast("Wallpaper set!")
                return
            except:
                continue
        self.show_toast("Could not set wallpaper automatically")

    def show_toast(self, message):
        old = self.filename_label.get_text()
        self.filename_label.set_text(message)
        GLib.timeout_add_seconds(2, lambda: self.filename_label.set_text(
            os.path.basename(self.current_file) if self.current_file else old) or False)

    def show_error(self, message):
        dialog = Adw.MessageDialog.new(self, "Error")
        dialog.set_body(message)
        dialog.add_response("ok", "OK")
        dialog.present()


class LuminaApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.lumina.PhotoViewer',
                        flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.window = None
        self.create_action('flip_h', lambda a, p: self.window.canvas.toggle_flip_h())
        self.create_action('flip_v', lambda a, p: self.window.canvas.toggle_flip_v())
        self.create_action('brightness_contrast', lambda a, p: self.window.show_brightness_contrast_dialog())
        self.create_action('set_wallpaper', lambda a, p: self.window.set_wallpaper())
        self.create_action('quit', lambda a, p: self.quit(), ['<primary>q'])

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect('activate', callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)

    def do_activate(self):
        if not self.window:
            self.window = LuminaWindow(self)
        self.window.present()

    def do_open(self, files, n_files, hint):
        self.activate()
        if n_files > 0:
            path = files[0].get_path()
            if path:
                self.window.open_file(path)


def main():
    app = LuminaApp()
    return app.run(sys.argv)


if __name__ == '__main__':
    sys.exit(main())
