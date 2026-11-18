#!/usr/bin/env python3
from __future__ import annotations

"""GUI-independent runtime state initialization for the Linux client frontends."""

import queue
import threading


def initialize_client_runtime_state(client) -> None:
    """Initialize networking/session state shared by Tk and GTK clients.

    UI variables/widgets intentionally stay in their frontend constructors.  Any
    field used by the shared CoreClient networking methods belongs here so a new
    backend field cannot silently be omitted from the GTK frontend.
    """
    client._display_source_sizes = {}
    client._auto_display_id = 0
    client.remember_credentials_active = True
    client._connect_snapshot = None
    client._desktop_display_snapshot = None
    client._credential_serial = 0
    client._discovery_serial = 0
    client._discovery_busy = False
    client._update_check_busy = False
    client._update_check_serial = 0

    client.outgoing = queue.Queue(maxsize=1000)
    client._mouse_move_lock = threading.Lock()
    client._pending_mouse_move = None
    client.ui_queue = queue.Queue()
    client.stop_event = threading.Event()
    client.net_thread = None
    client.video_thread = None
    client.video_frames = queue.Queue(maxsize=1)
    client._video_reconfigure_event = threading.Event()
    client._video_reconfigure_not_before = 0.0
    client._video_viewport_width = 1600
    client._last_render_delay_ms = 0.0

    client.connected = False
    client.prelogin_session = False
    client.prelogin_video_ready = False
    client.prelogin_input_armed = False
    client.prelogin_input_ready = False
    client._last_prelogin_mouse_tx_log_at = 0.0
    client._last_prelogin_input_blocked_log_at = 0.0
    client.server_runtime_version = ''
    client.server_runtime_matches_client = True
    client._desktop_session_token = ''
    client._desktop_session_generation = 0
    client.remote_image_size = (1, 1)
    client.display_rect = (0, 0, 1, 1)
    client.modifiers_down = set()
    client.keys_down = set()
    client.remote_path = ''
    client.file_entries = {}
    client.disconnect_requested = False
    client.fullscreen_active = False
    client.mouse_buttons_down = set()
