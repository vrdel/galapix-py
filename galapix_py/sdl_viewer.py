from __future__ import annotations

import ctypes
import ctypes.util
import time
from dataclasses import dataclass

import sdl2

from .viewer import FrameRenderStats, Viewer

APP_ID = b"galapix-py"


class XClassHint(ctypes.Structure):
    _fields_ = [
        ("res_name", ctypes.c_char_p),
        ("res_class", ctypes.c_char_p),
    ]


def configure_app_identity_hint(app_id: bytes = APP_ID) -> None:
    sdl2.SDL_SetHint(b"SDL_APP_NAME", app_id)


def set_x11_window_class(window: object, app_id: bytes = APP_ID) -> None:
    x11_library = ctypes.util.find_library("X11")
    if not x11_library:
        return
    wm_info = sdl2.SDL_SysWMinfo()
    sdl2.SDL_VERSION(wm_info.version)
    if not sdl2.SDL_GetWindowWMInfo(window, ctypes.byref(wm_info)):
        return
    if wm_info.subsystem != sdl2.SDL_SYSWM_X11:
        return
    x11 = ctypes.CDLL(x11_library)
    x11.XSetClassHint.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(XClassHint)]
    x11.XSetClassHint.restype = ctypes.c_int
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XFlush.restype = ctypes.c_int
    hint = XClassHint(res_name=app_id, res_class=app_id)
    if x11.XSetClassHint(wm_info.info.x11.display, wm_info.info.x11.window, ctypes.byref(hint)):
        x11.XFlush(wm_info.info.x11.display)


@dataclass(slots=True)
class LiveRenderValidation:
    timeout: float
    frames_seen: int = 0
    last_stats: FrameRenderStats | None = None

    def observe(self, elapsed: float, stats: FrameRenderStats) -> tuple[bool, str | None]:
        self.frames_seen += 1
        self.last_stats = stats
        if stats.textured_tiles > 0:
            return True, (
                "live render validation passed: "
                f"frames={self.frames_seen} visible_images={stats.visible_images} textured_tiles={stats.textured_tiles}"
            )
        if elapsed >= self.timeout:
            return False, (
                "live render validation timed out: "
                f"frames={self.frames_seen} visible_images={stats.visible_images} "
                f"textured_tiles={stats.textured_tiles} placeholder_tiles={stats.placeholder_tiles}"
            )
        return False, None

    def timeout_message(self, elapsed: float) -> str | None:
        if elapsed < self.timeout:
            return None
        stats = self.last_stats or FrameRenderStats()
        return (
            "live render validation timed out: "
            f"frames={self.frames_seen} visible_images={stats.visible_images} "
            f"textured_tiles={stats.textured_tiles} placeholder_tiles={stats.placeholder_tiles}"
        )


class SDLViewer:
    def __init__(
        self,
        viewer: Viewer,
        fullscreen: bool = False,
        validate_render: bool = False,
        validation_timeout: float = 5.0,
    ) -> None:
        self.viewer = viewer
        self.fullscreen = fullscreen
        self.validate_render = validate_render
        self.validation_timeout = validation_timeout
        self.window = None
        self.context = None
        self.running = False
        self.mouse_down_pos: tuple[int, int] | None = None
        self._last_title: str | None = None
        self._suppress_opening_search_text = False

    def _ctrl_pressed(self) -> bool:
        mods = sdl2.SDL_GetModState()
        return bool(mods & sdl2.KMOD_CTRL)

    def _shift_pressed(self) -> bool:
        mods = sdl2.SDL_GetModState()
        return bool(mods & sdl2.KMOD_SHIFT)

    def _wheel_zoom_factor(self) -> float:
        if self._shift_pressed():
            return 1.5
        if self._ctrl_pressed():
            return 1.25
        return 1.1

    def _drag_pan_factor(self) -> float:
        if self._shift_pressed():
            return 3.0
        if self._ctrl_pressed():
            return 2.0
        return 1.0

    def run(self) -> None:
        configure_app_identity_hint()
        sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO)
        sdl2.SDL_GL_SetAttribute(sdl2.SDL_GL_CONTEXT_MAJOR_VERSION, 2)
        sdl2.SDL_GL_SetAttribute(sdl2.SDL_GL_CONTEXT_MINOR_VERSION, 1)
        flags = sdl2.SDL_WINDOW_OPENGL | sdl2.SDL_WINDOW_RESIZABLE | sdl2.SDL_WINDOW_HIDDEN
        if self.fullscreen:
            flags |= sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP
        self.window = sdl2.SDL_CreateWindow(
            self.viewer.options.title.encode(),
            sdl2.SDL_WINDOWPOS_CENTERED,
            sdl2.SDL_WINDOWPOS_CENTERED,
            self.viewer.options.width,
            self.viewer.options.height,
            flags,
        )
        if not self.window:
            raise RuntimeError(f"SDL_CreateWindow failed: {sdl2.SDL_GetError().decode()}")
        set_x11_window_class(self.window)
        self.context = sdl2.SDL_GL_CreateContext(self.window)
        if not self.context:
            raise RuntimeError(f"SDL_GL_CreateContext failed: {sdl2.SDL_GetError().decode()}")
        sdl2.SDL_ShowWindow(self.window)
        self.viewer.set_viewport(self.viewer.options.width, self.viewer.options.height)
        self.viewer.zoom_to_workspace()
        self.running = True
        event = sdl2.SDL_Event()
        last = time.monotonic()
        started = last
        validator = LiveRenderValidation(self.validation_timeout) if self.validate_render else None
        try:
            while self.running:
                had_event = False
                while sdl2.SDL_PollEvent(event):
                    had_event = True
                    self._process_event(event)
                self._process_keyboard_state()
                now = time.monotonic()
                delta = now - last
                last = now
                self.viewer.update(delta)
                if had_event or self.viewer.needs_redraw:
                    stats = self.viewer.draw()
                    self._update_title()
                    sdl2.SDL_GL_SwapWindow(self.window)
                    if validator is not None:
                        success, message = validator.observe(now - started, stats)
                        if message is not None:
                            if success:
                                print(message)
                                self.running = False
                            else:
                                raise RuntimeError(message)
                if validator is not None:
                    timeout_message = validator.timeout_message(now - started)
                    if timeout_message is not None:
                        raise RuntimeError(timeout_message)
                sdl2.SDL_Delay(10)
        finally:
            sdl2.SDL_StopTextInput()
            sdl2.SDL_GL_DeleteContext(self.context)
            sdl2.SDL_DestroyWindow(self.window)
            sdl2.SDL_Quit()

    def _process_keyboard_state(self) -> None:
        if getattr(self.viewer, "search_active", False):
            return
        numkeys = ctypes.c_int()
        keystate = sdl2.SDL_GetKeyboardState(ctypes.byref(numkeys))

        def pressed(key: int) -> bool:
            scancode = sdl2.SDL_GetScancodeFromKey(key)
            return bool(keystate[scancode])

        ctrl = self._ctrl_pressed()
        shift = self._shift_pressed()
        alt = pressed(sdl2.SDLK_LALT) or pressed(sdl2.SDLK_RALT)
        if alt:
            return

        pan_step = 48.0 if shift else (32.0 if ctrl else 16.0)
        zoom_factor = 1.3 if shift else (1.15 if ctrl else 1.05)
        moved = False

        if pressed(sdl2.SDLK_LEFT):
            self.viewer.state.move(+pan_step, 0.0)
            moved = True
        if pressed(sdl2.SDLK_RIGHT):
            self.viewer.state.move(-pan_step, 0.0)
            moved = True
        if pressed(sdl2.SDLK_UP):
            self.viewer.state.move(0.0, +pan_step)
            moved = True
        if pressed(sdl2.SDLK_DOWN):
            self.viewer.state.move(0.0, -pan_step)
            moved = True
        if pressed(sdl2.SDLK_w):
            self.viewer.state.zoom(zoom_factor, self.viewer.viewport_width / 2.0, self.viewer.viewport_height / 2.0)
            moved = True
        if pressed(sdl2.SDLK_s):
            self.viewer.state.zoom(1.0 / zoom_factor, self.viewer.viewport_width / 2.0, self.viewer.viewport_height / 2.0)
            moved = True

        if moved:
            self.viewer.request_redraw()

    def _process_event(self, event: sdl2.SDL_Event) -> None:
        if event.type == sdl2.SDL_QUIT:
            self.running = False
        elif event.type == sdl2.SDL_TEXTINPUT and getattr(self.viewer, "search_active", False):
            raw = bytes(event.text.text)
            text = raw.split(b"\0", 1)[0].decode("utf-8", errors="ignore")
            if self._suppress_opening_search_text and text == "/":
                self._suppress_opening_search_text = False
                return
            self._suppress_opening_search_text = False
            self.viewer.append_search_text(text)
        elif event.type == sdl2.SDL_MOUSEWHEEL:
            if getattr(self.viewer, "search_active", False):
                return
            mouse_x, mouse_y = ctypes.c_int(), ctypes.c_int()
            sdl2.SDL_GetMouseState(mouse_x, mouse_y)
            wheel_zoom_factor = self._wheel_zoom_factor()
            factor = wheel_zoom_factor if event.wheel.y > 0 else (1.0 / wheel_zoom_factor)
            self.viewer.state.zoom(factor, mouse_x.value, mouse_y.value)
            self.viewer.request_redraw()
        elif event.type == sdl2.SDL_MOUSEBUTTONDOWN:
            if getattr(self.viewer, "search_active", False):
                return
            if event.button.button == sdl2.SDL_BUTTON_LEFT:
                self.mouse_down_pos = (event.button.x, event.button.y)
        elif event.type == sdl2.SDL_MOUSEMOTION:
            if getattr(self.viewer, "search_active", False):
                return
            left_drag = bool(event.motion.state & sdl2.SDL_BUTTON_LMASK)
            right_drag = bool(event.motion.state & sdl2.SDL_BUTTON_RMASK)
            middle_drag = bool(event.motion.state & sdl2.SDL_BUTTON_MMASK)
            if left_drag or right_drag or middle_drag:
                drag_pan_factor = self._drag_pan_factor()
                if middle_drag:
                    drag_pan_factor = max(3.0, drag_pan_factor)
                if right_drag:
                    drag_pan_factor = max(2.0, drag_pan_factor)
                self.viewer.state.move(event.motion.xrel * drag_pan_factor, event.motion.yrel * drag_pan_factor)
                self.viewer.request_redraw()
        elif event.type == sdl2.SDL_MOUSEBUTTONUP:
            if getattr(self.viewer, "search_active", False):
                return
            if event.button.button == sdl2.SDL_BUTTON_LEFT and self.mouse_down_pos is not None:
                dx = event.button.x - self.mouse_down_pos[0]
                dy = event.button.y - self.mouse_down_pos[1]
                if abs(dx) <= 3 and abs(dy) <= 3:
                    self.viewer.select_at_screen(event.button.x, event.button.y)
                self.mouse_down_pos = None
        elif event.type == sdl2.SDL_WINDOWEVENT and event.window.event == sdl2.SDL_WINDOWEVENT_SIZE_CHANGED:
            self.viewer.set_viewport(event.window.data1, event.window.data2)
            self.viewer.request_redraw()
        elif event.type == sdl2.SDL_KEYDOWN:
            sym = event.key.keysym.sym
            if getattr(self.viewer, "search_active", False):
                if sym == sdl2.SDLK_ESCAPE:
                    self.viewer.close_search(clear=True)
                    self._suppress_opening_search_text = False
                    sdl2.SDL_StopTextInput()
                elif sym == sdl2.SDLK_RETURN:
                    self.viewer.close_search(clear=False)
                    self._suppress_opening_search_text = False
                    sdl2.SDL_StopTextInput()
                elif sym == sdl2.SDLK_BACKSPACE:
                    self.viewer.backspace_search()
                return
            if sym == sdl2.SDLK_ESCAPE:
                self.running = False
            elif sym == sdl2.SDLK_SLASH:
                self.viewer.open_search()
                self._suppress_opening_search_text = True
                sdl2.SDL_StartTextInput()
            elif sym == sdl2.SDLK_h:
                self.viewer.zoom_home()
                self.viewer.request_redraw()
            elif sym == sdl2.SDLK_x:
                self.viewer.zoom_to_selection()
                self.viewer.request_redraw()
            elif sym == sdl2.SDLK_n:
                self.viewer.zoom_to_original()
                self.viewer.request_redraw()
            elif sym == sdl2.SDLK_b:
                shift = bool(event.key.keysym.mod & sdl2.KMOD_SHIFT)
                self.viewer.cycle_background(backwards=shift)
            elif sym == sdl2.SDLK_c:
                self.viewer.clear_all_caches()
            elif sym == sdl2.SDLK_F1:
                self.viewer.toggle_status()
            elif sym == sdl2.SDLK_F2:
                self.viewer.load_workspace()
            elif sym == sdl2.SDLK_F3:
                self.viewer.save_workspace()
            elif sym == sdl2.SDLK_F5:
                self.viewer.refresh_selection()
            elif sym == sdl2.SDLK_1:
                shift = bool(event.key.keysym.mod & sdl2.KMOD_SHIFT)
                self.viewer.workspace.sort_by_url(reverse=shift, case_insensitive=self.viewer.options.case_insensitive_sort)
                self.viewer.workspace.layout_row(
                    spacing=40.0 * max(1, self.viewer.options.spacing),
                    max_per_row=self.viewer.options.images_per_row,
                    images=self.viewer.workspace.filtered_images() if self.viewer.has_active_filter() else None,
                )
                self.viewer.request_redraw()
            elif sym == sdl2.SDLK_2:
                shift = bool(event.key.keysym.mod & sdl2.KMOD_SHIFT)
                self.viewer.workspace.sort_by_mtime(reverse=shift)
                self.viewer.workspace.layout_row(
                    spacing=40.0 * max(1, self.viewer.options.spacing),
                    max_per_row=self.viewer.options.images_per_row,
                    images=self.viewer.workspace.filtered_images() if self.viewer.has_active_filter() else None,
                )
                self.viewer.request_redraw()
            elif sym == sdl2.SDLK_i:
                self.viewer.workspace.isolate_selection()
                self.viewer.workspace.layout_row(
                    spacing=40.0 * max(1, self.viewer.options.spacing),
                    max_per_row=self.viewer.options.images_per_row,
                )
                self.viewer.zoom_to_workspace()
                self.viewer.request_redraw()
            elif sym == sdl2.SDLK_DELETE:
                self.viewer.workspace.delete_selection()
                self.viewer.workspace.layout_row(
                    spacing=40.0 * max(1, self.viewer.options.spacing),
                    max_per_row=self.viewer.options.images_per_row,
                )
                self.viewer.zoom_to_workspace()
                self.viewer.request_redraw()
            elif sym == sdl2.SDLK_SPACE:
                self.viewer.print_visible_images()
            elif sym == sdl2.SDLK_0:
                self.viewer.print_info()

    def _update_title(self) -> None:
        title = self.viewer.status_text() if self.viewer.show_status else self.viewer.options.title
        if title != self._last_title:
            sdl2.SDL_SetWindowTitle(self.window, title.encode("utf-8"))
            self._last_title = title
