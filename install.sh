#!/bin/bash
# Lumina Photo Viewer — Official Grade Installer
# Supports: Fedora, Ubuntu, Arch, and any GTK4-based Linux desktop

set -e

echo "╔══════════════════════════════════════════════════════╗"
echo "║     Lumina Photo Viewer — Official Grade            ║"
echo "║     Pinch-to-Zoom • Smooth Pan • Crop & Edit        ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# Detect install mode
if [ "$EUID" -eq 0 ]; then
    INSTALL_MODE="system"
    PREFIX="/usr/local"
    DESKTOP_DIR="/usr/share/applications"
    ICON_DIR="/usr/share/icons/hicolor/scalable/apps"
else
    INSTALL_MODE="user"
    PREFIX="$HOME/.local"
    DESKTOP_DIR="$HOME/.local/share/applications"
    ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
    mkdir -p "$HOME/.local/bin"
    echo "⚠️  User install mode. Add $HOME/.local/bin to your PATH if not already."
    echo ""
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Dependencies ──
echo "📦 Checking dependencies..."

MISSING=""

# Check for Python GObject
python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')" 2>/dev/null || MISSING="$MISSING python3-gobject"

# Check for GTK4
gtk4-launch --version >/dev/null 2>&1 || MISSING="$MISSING gtk4"

# Check for libadwaita
python3 -c "import gi; gi.require_version('Adw', '1')" 2>/dev/null || MISSING="$MISSING libadwaita"

if [ -n "$MISSING" ]; then
    echo "❌ Missing packages:$MISSING"
    echo ""
    if command -v dnf &>/dev/null; then
        echo "Install with: sudo dnf install python3-gobject python3-cairo gtk4 libadwaita gdk-pixbuf2-loader-webp"
    elif command -v apt &>/dev/null; then
        echo "Install with: sudo apt install python3-gi python3-cairo gir1.2-gtk-4.0 gir1.2-adw-1"
    elif command -v pacman &>/dev/null; then
        echo "Install with: sudo pacman -S python-gobject gtk4 libadwaita"
    fi
    echo ""
    read -p "Continue anyway? (y/N): " choice
    [[ "$choice" =~ ^[Yy]$ ]] || exit 1
fi

# ── Install ──
echo ""
echo "📁 Installing to $PREFIX ..."

mkdir -p "$PREFIX/bin"
mkdir -p "$DESKTOP_DIR"
mkdir -p "$ICON_DIR"

# Copy app
cp "$SCRIPT_DIR/lumina.py" "$PREFIX/bin/lumina"
chmod +x "$PREFIX/bin/lumina"

# Create desktop entry
cat > "$DESKTOP_DIR/com.lumina.PhotoViewer.desktop" << 'EOF'
[Desktop Entry]
Name=Lumina Photo Viewer
Comment=Modern image viewer with pinch zoom, crop, and edit
Exec=lumina %f
Icon=lumina-icon
Type=Application
MimeType=image/jpeg;image/png;image/webp;image/bmp;image/tiff;image/gif;image/svg+xml;image/x-portable-pixmap;image/x-portable-bitmap;image/x-portable-greymap;image/x-xbitmap;image/x-xpixmap;
Categories=Graphics;Viewer;Photography;GTK;
Keywords=image;photo;picture;viewer;gallery;zoom;crop;edit;pinch;
StartupNotify=true
Terminal=false
Actions=NewWindow;

[Desktop Action NewWindow]
Name=Open New Window
Exec=lumina
EOF

# Create SVG icon
cat > "$ICON_DIR/lumina-icon.svg" << 'EOF'
<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#4f8ef7"/>
      <stop offset="100%" stop-color="#1e5bc6"/>
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="28" fill="url(#bg)"/>
  <circle cx="64" cy="52" r="26" fill="white" opacity="0.95"/>
  <path d="M20 98 L42 64 L64 86 L86 56 L108 98 Z" fill="white" opacity="0.85"/>
  <circle cx="92" cy="36" r="10" fill="#ffd700" opacity="0.9"/>
</svg>
EOF

# Update caches
if [ "$INSTALL_MODE" = "system" ]; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database /usr/share/applications 2>/dev/null || true
else
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
fi

echo ""
echo "✅ Installation complete!"
echo ""
echo "🚀 Launch: lumina [image-file]"
echo ""
echo "📌 Set as default:"
echo "   Right-click image → Open With → Lumina → Set as default"
echo ""
echo "⌨️  Shortcuts:"
echo "   Ctrl+O          Open"
echo "   Ctrl++/−        Zoom in/out"
echo "   Ctrl+0          Fit to window"
echo "   Ctrl+L/R        Rotate"
echo "   Ctrl+Shift+C    Crop tool"
echo "   ←/→             Prev/Next image"
echo "   I               Image info"
echo "   Two-finger pinch  Zoom (touchpad)"
echo "   Click-drag      Pan (when zoomed)"
echo ""
