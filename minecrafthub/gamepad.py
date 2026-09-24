"""bol.gamepad — read a game controller straight from the Linux input layer.

The launcher window is the one screen a controller user cannot get past: in
Steam Game Mode, on a Steam Deck, or on any couch setup there is no mouse to
click PLAY with.  This module is the input half of the answer; `bol.navigation`
turns what it reports into a moving focus ring over the Qt window.

The two halves are deliberately separable, and the split has already paid for
itself once: the PySide6 rewrite deleted the original ring, which was built on
Tk internals, without touching a line of this file, and the Qt replacement
reads controllers through exactly the same interface.

It talks to ``/dev/input/event*`` directly instead of pulling in SDL, pygame or
python-evdev.  The launcher ships as a .deb, an AppImage, a Flatpak and a
single-file .pyz, and a dependency that has to be present in all four — and
importable *before* the GUI toolkit finishes bootstrapping — is a much larger
liability than the ~100 lines of struct and ioctl work below.  The kernel ABI
used here (``input_event``, ``EVIOCGBIT``, ``EVIOCGABS``) has been stable since
Linux 2.6.

Reading the device nodes needs no special privilege in practice: udev tags
every ``ID_INPUT_JOYSTICK`` device with ``uaccess`` (70-uaccess.rules), so the
logged-in seat owner gets an ACL on the node.  The Flatpak build already
carries ``--device=all`` for the game's own controller support, which covers
this too.  A user who is somehow neither gets a one-time warning suggesting the
``input`` group rather than a stack trace.

Everything is normalised to a handful of logical action names, so the GUI never
sees a button code:

    up / down / left / right      d-pad, hat switch or left stick, auto-repeat
    accept / back                 A/Cross, B/Circle
    alt / menu                    X/Square, Y/Triangle
    prev_tab / next_tab           left and right shoulder
    start / select / guide        the three system buttons
    scroll_up / scroll_down       right stick, fast auto-repeat

Layouts differ on which physical button carries which letter, so the mapping is
positional throughout: ``accept`` is always the bottom face button and ``back``
the right-hand one, which is what both the Xbox and PlayStation conventions
agree on.
"""
# SPDX-License-Identifier: MIT

import errno
import os
import select
import struct
import threading
import time

from .log import warn

INPUT_DIR = "/dev/input"

# --- kernel input ABI ----------------------------------------------------
EV_KEY = 0x01
EV_ABS = 0x03

# struct input_event { struct timeval time; __u16 type, code; __s32 value; }.
# Python's native struct format picks the platform's C long, so one format
# string covers both the 16-byte 32-bit layout and the 24-byte 64-bit one.
_EVENT = struct.Struct("llHHi")

# struct input_absinfo { __s32 value, minimum, maximum, fuzz, flat, resolution }
_ABSINFO = struct.Struct("6i")

BTN_TRIGGER = 0x120           # plain joystick fire button
BTN_THUMB = 0x121
BTN_SOUTH = 0x130             # A / Cross      (a.k.a. BTN_A)
BTN_EAST = 0x131              # B / Circle
BTN_NORTH = 0x133             # Y / Triangle   (the legacy alias calls it BTN_X)
BTN_WEST = 0x134              # X / Square
BTN_TL = 0x136
BTN_TR = 0x137
BTN_SELECT = 0x13A
BTN_START = 0x13B
BTN_MODE = 0x13C
BTN_DPAD_UP = 0x220
BTN_DPAD_DOWN = 0x221
BTN_DPAD_LEFT = 0x222
BTN_DPAD_RIGHT = 0x223

ABS_X = 0x00
ABS_Y = 0x01
ABS_RY = 0x04
ABS_HAT0X = 0x10
ABS_HAT0Y = 0x11

# Buttons that fire once per press.
_BUTTONS = {
    BTN_TRIGGER: "accept",
    BTN_THUMB: "back",
    BTN_SOUTH: "accept",
    BTN_EAST: "back",
    BTN_WEST: "alt",
    BTN_NORTH: "menu",
    BTN_TL: "prev_tab",
    BTN_TR: "next_tab",
    BTN_SELECT: "select",
    BTN_START: "start",
    BTN_MODE: "guide",
}

# Buttons that behave like a direction: held down, they repeat.
_DPAD_BUTTONS = {
    BTN_DPAD_UP: "up",
    BTN_DPAD_DOWN: "down",
    BTN_DPAD_LEFT: "left",
    BTN_DPAD_RIGHT: "right",
}

# Axis -> (negative direction, positive direction, channel). The channel keeps
# menu movement and scrolling on separate repeat timers: a stick flick should
# step one item, while the scroll stick should keep the text sliding.
_AXES = {
    ABS_X: ("left", "right", "nav"),
    ABS_Y: ("up", "down", "nav"),
    ABS_HAT0X: ("left", "right", "nav"),
    ABS_HAT0Y: ("up", "down", "nav"),
    ABS_RY: ("scroll_up", "scroll_down", "scroll"),
}

# Deflection at which an axis starts counting as a direction, and the lower
# value it has to fall back through before it counts as released. The gap is
# what stops a stick resting near the threshold from machine-gunning the menu.
_AXIS_PRESS = 0.55
_AXIS_RELEASE = 0.35


def _ioc(direction, type_char, number, size):
    """Encode an ioctl request number the way <asm-generic/ioctl.h> does."""
    return (direction << 30) | (size << 16) | (ord(type_char) << 8) | number


_IOC_READ = 2


def _EVIOCGNAME(length):
    return _ioc(_IOC_READ, "E", 0x06, length)


def _EVIOCGBIT(event_type, length):
    return _ioc(_IOC_READ, "E", 0x20 + event_type, length)


def _EVIOCGABS(axis):
    return _ioc(_IOC_READ, "E", 0x40 + axis, _ABSINFO.size)


def _ioctl_bytes(fd, request, length):
    """Run a reading ioctl into a fresh buffer, or return None on failure."""
    import fcntl
    buf = bytearray(length)
    try:
        fcntl.ioctl(fd, request, buf)
    except OSError:
        return None
    return buf


def _bit_set(bits, code):
    index = code >> 3
    return bool(bits) and index < len(bits) and bool(bits[index] & (1 << (code & 7)))


def device_name(fd):
    """The device's advertised name, or "" when the driver has none."""
    raw = _ioctl_bytes(fd, _EVIOCGNAME(256), 256)
    if not raw:
        return ""
    return bytes(raw).split(b"\0", 1)[0].decode("utf-8", "replace").strip()


def looks_like_gamepad(fd):
    """Whether an open event node is a game controller rather than a keyboard.

    The same test the input stack itself uses: gamepad face buttons plus
    absolute axes.  Requiring both is what keeps keyboards, mice, tablets and
    the accelerometers laptops expose out of the candidate list.
    """
    supported = _ioctl_bytes(fd, _EVIOCGBIT(0, 4), 4)          # EV_MAX = 0x1f
    if not (_bit_set(supported, EV_KEY) and _bit_set(supported, EV_ABS)):
        return False
    keys = _ioctl_bytes(fd, _EVIOCGBIT(EV_KEY, 96), 96)        # KEY_MAX = 0x2ff
    if not any(_bit_set(keys, code)
               for code in (BTN_SOUTH, BTN_TRIGGER, BTN_DPAD_UP)):
        return False
    axes = _ioctl_bytes(fd, _EVIOCGBIT(EV_ABS, 8), 8)          # ABS_MAX = 0x3f
    return ((_bit_set(axes, ABS_X) and _bit_set(axes, ABS_Y))
            or _bit_set(axes, ABS_HAT0X))


def _axis_range(fd, axis):
    """(minimum, maximum) for an absolute axis, or None when unreadable."""
    raw = _ioctl_bytes(fd, _EVIOCGABS(axis), _ABSINFO.size)
    if not raw:
        return None
    _value, minimum, maximum, _fuzz, _flat, _resolution = _ABSINFO.unpack(
        bytes(raw))
    if maximum <= minimum:
        return None
    return minimum, maximum


PROC_DEVICES = "/proc/bus/input/devices"


def joystick_event_nodes(path=PROC_DEVICES):
    """Event node names the kernel's joystick driver also claimed.

    Needed to tell "no controller is plugged in" from "a controller is plugged
    in and this user may not open it". Device nodes are not world-readable, but
    this file is, and the joydev handler is attached to exactly the devices the
    input core classifies as joysticks — so a device listed with both a ``js``
    and an ``event`` handler is a controller whatever our own ``open`` says.
    Distributions that do not load joydev report nothing here, which reads as
    "no controller" and is no worse than not looking.
    """
    nodes = set()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read()
    except OSError:
        return nodes
    for block in content.split("\n\n"):
        handlers = set()
        for line in block.splitlines():
            if line.startswith("H: Handlers="):
                handlers = set(line.split("=", 1)[1].split())
        if any(name.startswith("js") for name in handlers):
            nodes |= {name for name in handlers if name.startswith("event")}
    return nodes


# What `_probe` answers with for a node it read and that is not a controller,
# as opposed to one it could not read at all.
NOT_A_GAMEPAD = "not-a-gamepad"


class _Device:
    """One open controller node and the axis state we track for it."""

    def __init__(self, fd, path, name, axes):
        self.fd = fd
        self.path = path
        self.name = name
        self.axes = axes            # code -> (minimum, maximum)
        self.held = {}              # axis code / button code -> direction

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


def normalize_axis(value, minimum, maximum):
    """An axis reading as -1.0 … 1.0 around its resting centre."""
    span = (maximum - minimum) / 2.0
    if span <= 0:
        return 0.0
    centre = (maximum + minimum) / 2.0
    return max(-1.0, min(1.0, (value - centre) / span))


def axis_direction(value, minimum, maximum, previous=None):
    """Which direction an axis reading means, with hysteresis.

    `previous` is the direction this axis was last reported at; passing it back
    in is what applies the release threshold instead of the press one.
    """
    level = normalize_axis(value, minimum, maximum)
    threshold = _AXIS_RELEASE if previous else _AXIS_PRESS
    if level <= -threshold:
        return "negative"
    if level >= threshold:
        return "positive"
    return None


class GamepadReader:
    """Watches every controller on the system and reports logical actions.

    `on_action(name)` is called from this object's own thread, once per press
    and again on each auto-repeat, so a Tk consumer must hand the name over to
    the main loop rather than touching widgets from the callback.
    `on_devices(names)` reports the connected set whenever it changes.
    """

    REPEAT_DELAY = 0.40           # before a held direction starts repeating
    REPEAT_INTERVAL = 0.11        # between repeats afterwards
    SCROLL_INTERVAL = 0.05        # the scroll stick repeats much faster
    RESCAN_INTERVAL = 2.0         # hot-plug polling

    def __init__(self, on_action, on_devices=None, input_dir=INPUT_DIR):
        self._on_action = on_action
        self._on_devices = on_devices
        self._input_dir = input_dir
        self._devices = {}                     # path -> _Device
        self._ignored = {}                     # path -> identity, not a pad
        self._repeats = {}                     # channel -> [direction, due]
        self._thread = None
        self._stop = threading.Event()
        self._wake_r = self._wake_w = None
        self._warned_permission = False

    # -- lifecycle --------------------------------------------------------
    def start(self):
        """Begin watching. Returns False when the input layer is unreachable."""
        if self._thread is not None:
            return True
        if not os.path.isdir(self._input_dir):
            return False
        self._stop.clear()
        self._wake_r, self._wake_w = os.pipe()
        self._thread = threading.Thread(
            target=self._run, name="bol-gamepad", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        if self._wake_w is not None:
            try:
                os.write(self._wake_w, b"\0")
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        for pipe_end in (self._wake_r, self._wake_w):
            if pipe_end is not None:
                try:
                    os.close(pipe_end)
                except OSError:
                    pass
        self._wake_r = self._wake_w = None
        for device in list(self._devices.values()):
            device.close()
        self._devices.clear()
        self._ignored.clear()
        self._repeats.clear()

    @property
    def device_names(self):
        return tuple(device.name or device.path
                     for device in self._devices.values())

    # -- discovery --------------------------------------------------------
    def _candidate_paths(self):
        try:
            entries = sorted(os.listdir(self._input_dir))
        except OSError:
            return []
        return [os.path.join(self._input_dir, entry) for entry in entries
                if entry.startswith("event")]

    def _probe(self, path):
        """Classify one event node.

        Returns a `_Device` for a controller, `NOT_A_GAMEPAD` for a node that
        was read and is something else, and None when it could not be opened
        at all — which is not the same answer, because udev applies the ACL a
        moment after creating the node and a pad plugged in right now is
        briefly unreadable.
        """
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EPERM):
                self._note_permission_problem(path)
            return None
        try:
            if not looks_like_gamepad(fd):
                os.close(fd)
                return NOT_A_GAMEPAD
            axes = {}
            for axis in _AXES:
                axis_range = _axis_range(fd, axis)
                if axis_range is not None:
                    axes[axis] = axis_range
            return _Device(fd, path, device_name(fd), axes)
        except OSError:
            try:
                os.close(fd)
            except OSError:
                pass
            return None

    @staticmethod
    def _identity(path):
        """Which node this is, so a recycled name is probed again."""
        try:
            info = os.stat(path)
        except OSError:
            return None
        return (info.st_dev, info.st_ino, info.st_rdev)

    def _note_permission_problem(self, path):
        """Warn only when the node refused really is a controller.

        Keyboards and mice are never readable by ordinary users — udev grants
        the seat owner an ACL on joysticks and deliberately not on the devices
        that could be used to log keystrokes — so warning about every refusal
        would put a permission error in front of every user on every launch.
        """
        if self._warned_permission:
            return
        if os.path.basename(path) not in joystick_event_nodes():
            return
        self._warned_permission = True
        warn(f"No permission to read the controller at {path}. Controller "
             "navigation stays off; adding your user to the 'input' group "
             "fixes it.")

    def _rescan(self):
        """Adopt newly plugged controllers and drop unplugged ones.

        Nodes already known not to be controllers are remembered so they are
        not reopened twice a second forever: opening an input device can wake
        the hardware behind it, and a laptop or a Deck should not be kept from
        suspending its USB ports by a launcher looking for a pad. The memo is
        keyed on the node's identity, so a device number reused by something
        else is examined again.
        """
        changed = False
        present = set()
        for path in self._candidate_paths():
            present.add(path)
            if path in self._devices:
                continue
            identity = self._identity(path)
            if identity is not None and self._ignored.get(path) == identity:
                continue
            found = self._probe(path)
            if found is NOT_A_GAMEPAD:
                if identity is not None:
                    self._ignored[path] = identity
            elif found is not None:
                self._devices[path] = found
                changed = True
        for path in list(self._ignored):
            if path not in present:
                self._ignored.pop(path, None)
        for path in list(self._devices):
            if path not in present:
                self._devices.pop(path).close()
                changed = True
        if changed and self._on_devices is not None:
            try:
                self._on_devices(self.device_names)
            except Exception:
                pass

    # -- event decoding ---------------------------------------------------
    def _emit(self, action):
        try:
            self._on_action(action)
        except Exception:
            pass

    def _press_direction(self, channel, direction):
        """Report a direction and arm its auto-repeat."""
        interval = (self.SCROLL_INTERVAL if channel == "scroll"
                    else self.REPEAT_DELAY)
        self._repeats[channel] = [direction, time.monotonic() + interval]
        self._emit(direction)

    def _release_direction(self, channel, direction):
        held = self._repeats.get(channel)
        if held is not None and held[0] == direction:
            self._repeats.pop(channel, None)

    def _handle_event(self, device, event_type, code, value):
        if event_type == EV_KEY:
            if code in _DPAD_BUTTONS:
                direction = _DPAD_BUTTONS[code]
                if value:
                    device.held[code] = direction
                    self._press_direction("nav", direction)
                else:
                    device.held.pop(code, None)
                    self._release_direction("nav", direction)
            elif value == 1 and code in _BUTTONS:
                self._emit(_BUTTONS[code])
            return
        if event_type != EV_ABS or code not in _AXES:
            return
        axis_range = device.axes.get(code)
        if axis_range is None:
            return
        negative, positive, channel = _AXES[code]
        previous = device.held.get(code)
        side = axis_direction(value, axis_range[0], axis_range[1],
                              previous=previous)
        direction = {"negative": negative, "positive": positive}.get(side)
        if direction == previous:
            return
        if previous is not None:
            device.held.pop(code, None)
            self._release_direction(channel, previous)
        if direction is not None:
            device.held[code] = direction
            self._press_direction(channel, direction)

    def _read(self, device):
        """Drain one device. Returns False when it has gone away."""
        try:
            data = os.read(device.fd, _EVENT.size * 64)
        except BlockingIOError:
            return True
        except OSError:
            return False
        if not data:
            return False
        for offset in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
            _sec, _usec, event_type, code, value = _EVENT.unpack_from(
                data, offset)
            self._handle_event(device, event_type, code, value)
        return True

    def _due_repeats(self, now):
        """Fire every auto-repeat that has come due and re-arm it."""
        for channel, held in list(self._repeats.items()):
            direction, due = held
            if now < due:
                continue
            interval = (self.SCROLL_INTERVAL if channel == "scroll"
                        else self.REPEAT_INTERVAL)
            held[1] = now + interval
            self._emit(direction)

    def _next_deadline(self, now, next_scan):
        deadline = next_scan
        for _direction, due in self._repeats.values():
            deadline = min(deadline, due)
        return max(0.0, deadline - now)

    # -- main loop --------------------------------------------------------
    def _run(self):
        next_scan = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_scan:
                self._rescan()
                next_scan = now + self.RESCAN_INTERVAL
                now = time.monotonic()
            watched = [device.fd for device in self._devices.values()]
            watched.append(self._wake_r)
            try:
                ready, _w, _x = select.select(
                    watched, [], [], self._next_deadline(now, next_scan))
            except (OSError, ValueError):
                # A device vanished between select() and the syscall; the next
                # rescan cleans the table up.
                self._rescan()
                continue
            if self._wake_r in ready:
                try:
                    os.read(self._wake_r, 64)
                except OSError:
                    pass
                if self._stop.is_set():
                    break
            for device in list(self._devices.values()):
                if device.fd in ready and not self._read(device):
                    self._devices.pop(device.path, None)
                    device.close()
                    if self._on_devices is not None:
                        try:
                            self._on_devices(self.device_names)
                        except Exception:
                            pass
            self._due_repeats(time.monotonic())


def connected(input_dir=INPUT_DIR, proc_devices=PROC_DEVICES):
    """(controller names, controllers that could not be opened).

    Cheap enough to call before building any UI: it opens each event node,
    asks the driver two questions and closes it again.
    """
    names = []
    refused = 0
    joysticks = joystick_event_nodes(proc_devices)
    entries = sorted(os.listdir(input_dir)) if os.path.isdir(input_dir) else []
    for entry in entries:
        if not entry.startswith("event"):
            continue
        try:
            fd = os.open(os.path.join(input_dir, entry),
                         os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EPERM) and entry in joysticks:
                refused += 1
            continue
        try:
            if looks_like_gamepad(fd):
                names.append(device_name(fd) or entry)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
    return names, refused


def available(input_dir=INPUT_DIR):
    """Whether at least one controller is readable right now."""
    return bool(connected(input_dir)[0])


def summary(input_dir=INPUT_DIR, proc_devices=PROC_DEVICES):
    """One line for `doctor`: which controllers the launcher can read."""
    if not os.path.isdir(input_dir):
        return f"no {input_dir} (controller navigation unavailable)"
    names, refused = connected(input_dir, proc_devices)
    if names:
        return ", ".join(names)
    if refused:
        return (f"{refused} connected but not readable — add your user to "
                "the 'input' group")
    return "none connected"
