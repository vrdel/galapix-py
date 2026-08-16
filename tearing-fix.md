# Horizontal Tearing Investigation and Fix

Date: 2026-08-16  
Application: `galapix-py` SDL2/OpenGL viewer  
System: X11, FVWM3, AMDGPU, internal `eDP` panel

## Executive summary

The viewer displayed a horizontal discontinuity while panning. The line stayed
at the same physical screen height rather than following the viewer window or
image content. That observation identified the artifact as display scanout
tearing, not an image-tile seam or viewer geometry defect.

```text
Observed motion artifact
         |
         v
line follows content? ---- yes ---> image/tile defect
         |
         no
         v
line follows window? ----- yes ---> viewer/rendering seam
         |
         no
         v
line stays at screen Y ---------> scanout tearing
```

The viewer originally did not explicitly request OpenGL vsync. Strict vsync
was added with `SDL_GL_SetSwapInterval(1)` and verified by a live probe, but the
tear remained. The session used FVWM3 without an observed compositor, and the
AMDGPU output property was `TearFree: auto`.

The effective fix was:

```bash
xrandr --output eDP --set TearFree on
```

The property was read back as `TearFree: on`, after which panning no longer
tore.

## Symptom

The artifact appeared only while the viewport was moving:

```text
                 physical screen
        +----------------------------------+
        |                                  |
        |       galapix viewer             |
        |     +----------------------+     |
        |     | frame N              |     |
        +=====+======================+=====+ <- fixed physical Y
        |     |          frame N + 1 |     |
        |     +----------------------+     |
        |                                  |
        +----------------------------------+
```

During a static frame, both buffers contain essentially the same pixels, so a
mid-scanout transition is not obvious. During panning, successive frames place
the image at different horizontal coordinates, making the transition visible.

```text
static scene                          panning scene
------------                          -------------

frame N:     ABCDEFG                  frame N:       ABCDEFG
frame N+1:   ABCDEFG                  frame N+1:    ABCDEFG
             -------                               ^
same pixels across boundary            displacement exposes boundary
```

## Why screen-relative position was decisive

A tile seam is attached to image or window coordinates:

```text
before moving window                 after moving window

+------------------------+          +------------------------+
| +------------------+   |          |                        |
| | image            |   |          |     +----------------+ |
| |------------------|   |          |     | image          | |
| | tile seam        |   |          |     |----------------| |
| +------------------+   |          |     | tile seam      | |
+------------------------+          |     +----------------+ |
                                    +------------------------+
      seam moves with window/content
```

A scanout tear is attached to the physical display scan:

```text
before moving window                 after moving window

+------------------------+          +------------------------+
| +------------------+   |          |                        |
| |                  |   |          |     +----------------+ |
|=+==================+===| <- Y      |=====+================+==| <- same Y
| |                  |   |          |     |                | |
| +------------------+   |          |     +----------------+ |
+------------------------+          +------------------------+
```

The reported behavior matched the second diagram.

## Viewer rendering path

```text
keyboard/mouse input
         |
         v
ViewerState pan/zoom target
         |
         v
state interpolation
         |
         v
Viewer.draw()
         |
         +--> clear framebuffer
         +--> draw visible image tiles
         +--> draw selection and labels
         |
         v
SDL_GL_SwapWindow()
         |
         v
GLX / DRI3 Present
         |
         v
AMDGPU scanout
         |
         v
eDP panel, 1920x1200 at 60 Hz
```

The viewer redraws continuously while panning because its interpolated state is
changing. This makes synchronization errors repeat and become easy to see.

## What tearing means

A display scans a frame from top to bottom. If the displayed buffer changes
before scanout reaches the bottom, one refresh contains two logical frames:

```text
time ----------------------------------------------------------->

GPU       [ render N ][ render N+1 ][ render N+2 ]

display   top ------------------------------------------ bottom
                            ^
                            |
                    buffer transition

result    +-------------------------------------------+
          | upper portion from frame N                |
          +===========================================+ <- tear
          | lower portion from frame N+1              |
          +-------------------------------------------+
```

The fixed height is determined by when the buffer transition intersects the
panel's scanout, not by any particular image row.

## Investigation timeline

```text
horizontal artifact reported
            |
            v
inspect tile and presentation code
            |
            +--> viewer swaps OpenGL buffers
            +--> no explicit swap interval
            |
            v
add strict swap interval 1
            |
            v
run complete unit suite
            |
            +--> 146 tests passed
            |
            v
run live hidden OpenGL probe
            |
            +--> SDL set return code = 0
            +--> SDL get swap interval = 1
            |
            v
tear still present
            |
            v
ask whether line follows window or screen
            |
            +--> fixed at physical screen height
            +--> present only during panning
            |
            v
inspect X11 display stack
            |
            +--> X11 session
            +--> FVWM3 window manager
            +--> no compositor observed
            +--> AMDGPU / Radeon 780M / radeonsi
            +--> DRI3 and Present enabled
            +--> TearFree default = auto
            |
            v
force eDP TearFree = on
            |
            v
tear eliminated
```

## Application-side vsync safeguard

The viewer created its context and immediately entered the render loop:

```text
before
------

SDL_GL_CreateContext()
          |
          v
SDL_GL_SwapWindow()
```

It now explicitly requests strict vsync after context creation:

```python
def enable_vsync() -> bool:
    return sdl2.SDL_GL_SetSwapInterval(1) == 0
```

```text
after
-----

SDL_GL_CreateContext()
          |
          v
SDL_GL_SetSwapInterval(1)
          |
     +----+----+
     |         |
  success    failure
     |         |
     |         +--> print warning with SDL error
     v
SDL_GL_SwapWindow()
```

Swap interval meanings:

```text
interval 0    no synchronization; tearing allowed
interval 1    strict synchronization to vertical refresh
interval -1   adaptive synchronization; tearing may be allowed when late
```

Strict interval `1` was chosen because the objective was tear-free panning.
A unit test verifies the exact requested value.

The live probe returned:

```text
set_rc = 0
get    = 1
error  = <empty>
```

This proved the request was accepted and reported as active. Because tearing
still occurred, the remaining issue was below the application's swap API.

## Live system evidence

```text
+----------------------+---------------------------------------+
| component            | observed value                        |
+----------------------+---------------------------------------+
| session              | X11                                   |
| window manager       | FVWM3                                 |
| compositor           | none observed                         |
| GPU                  | AMD Radeon 780M                        |
| OpenGL driver path   | radeonsi / glamor                      |
| presentation         | DRI3 and X11 Present enabled           |
| active output        | eDP                                   |
| resolution           | 1920x1200                             |
| refresh              | 60.00 Hz                              |
| VRR capability       | available                             |
| TearFree before      | auto                                  |
| TearFree after       | on                                    |
+----------------------+---------------------------------------+
```

The Xorg log showed this stack:

```text
Linux amdgpu kernel driver
            |
            v
Xorg amdgpu DDX
            |
            +--> glamor acceleration
            +--> radeonsi
            +--> DRI3 enabled
            +--> Present enabled
            +--> TearFree property default: auto
```

## Successful display-side fix

Command applied to the active panel:

```bash
xrandr --output eDP --set TearFree on
```

Verified state:

```text
eDP connected 1920x1200+0+0
TearFree: on
vrr_capable: 1
1920x1200 ... 60.00 Hz
```

Conceptual effect:

```text
TearFree=auto in this session          TearFree=on
-----------------------------          -----------

application back buffer                application back buffer
          |                                      |
          v                                      v
X11 Present                              X11 Present
          |                                      |
          v                                      v
possible unsynchronized path             tear-protected display path
          |                                      |
          v                                      v
scanout can show two frames               scanout shows complete frame
```

The exact internal buffering and flipping mechanism depends on the Xorg and
AMDGPU versions. The important verified result is behavioral: forcing the
driver property to `on` removed the screen-relative tear.

## Why application vsync was not enough

There are multiple boundaries between rendering and physical scanout:

```text
application       SDL/GLX       X11 Present       AMDGPU       panel
     |                |               |               |           |
     | render frame   |               |               |           |
     +--------------->|               |               |           |
     | swap interval  |               |               |           |
     |<-------------->|               |               |           |
     |                +-------------->|               |           |
     |                | present pixmap+-------------->|           |
     |                |               |               +---------->|
     |                |               |               |  scanout  |
```

The SDL swap interval controls the application/GLX presentation cadence. It
does not guarantee that every later X11/DDX scanout path is tear-free in every
non-composited configuration. On this machine, the AMDGPU output property was
the decisive layer.

## Persistence across login

The live `xrandr` change may reset after:

```text
logout / login
reboot
X server restart
display hotplug
docking or undocking
mode reset
```

For user-level persistence, execute the successful command from the existing
FVWM3 startup configuration. A typical entry looks like:

```text
AddToFunc StartFunction
+ I Exec exec xrandr --output eDP --set TearFree on
```

Do not create a second `StartFunction` blindly. Locate the active FVWM config
and append the command to its existing startup path.

```text
X server starts
      |
      v
FVWM3 reads config
      |
      v
StartFunction runs
      |
      v
xrandr forces eDP TearFree on
      |
      v
viewer pans without scanout tearing
```

Output names may change when docking. List current names with:

```bash
xrandr --query
```

An external panel may be named `HDMI-A-0`, `DisplayPort-0`, or something else.
Each active output that exhibits tearing may need its own setting.

## Rollback

Restore the original automatic behavior:

```bash
xrandr --output eDP --set TearFree auto
```

Explicitly disable it for comparison:

```bash
xrandr --output eDP --set TearFree off
```

```text
            enable
     auto -----------> on
       ^                |
       |                |
       +----------------+
            rollback
```

The original observed value was `auto`.

## Future diagnostic checklist

```text
artifact appears during motion
              |
              v
does it remain when idle?
       |                 |
      yes               no
       |                 |
seam/corruption          v
                 does it follow content?
                    |             |
                   yes           no
                    |             |
               tile/image         v
                            does it follow window?
                               |             |
                              yes           no
                               |             |
                         viewer seam          v
                                      screen scanout tear
                                               |
                                               v
                                     verify swap interval
                                               |
                                               v
                                  compositor or TearFree layer
```

Useful commands:

```bash
# Session type
printf '%s\n' "$XDG_SESSION_TYPE"

# Outputs, modes, refresh rates, and TearFree properties
xrandr --verbose

# Find the window manager root window
xprop -root _NET_SUPPORTING_WM_CHECK

# Replace WINDOW_ID with the ID printed above
xprop -id WINDOW_ID _NET_WM_NAME WM_NAME

# Inspect Xorg driver initialization
rg -n 'AMDGPU|TearFree|Present|DRI3|radeonsi' \
  ~/.local/share/xorg/Xorg.*.log
```

Quick interpretation:

```text
line follows image content       -> source image or tile data
line follows viewer window       -> geometry or texture seam
line stays at physical screen Y  -> scanout synchronization
line appears only during motion  -> successive-frame mismatch / tearing
line persists while idle         -> stable seam, corruption, or overlay
```

## Verification performed

```text
source check
   |
   +--> strict SDL swap interval added
   +--> warning added for rejected vsync request
   +--> focused unit test added
   |
test check
   |
   +--> 146 tests passed
   +--> Python compilation passed
   +--> git diff whitespace check passed
   |
live display check
   |
   +--> swap interval probe reported 1
   +--> eDP TearFree property read back as on
   +--> user confirmed tearing eliminated
```

## Final conclusion

```text
root cause
   |
   v
X11 scanout tearing during animated panning
   |
   +--> not an image-tile boundary
   +--> not tied to viewer-window coordinates
   +--> persisted with accepted SDL swap interval 1
   |
   v
non-composited FVWM3 + AMDGPU TearFree=auto path
   |
   v
xrandr forces eDP TearFree=on
   |
   v
tear removed
```

The viewer-side vsync request is correct defensive behavior and should remain.
For this X11 session, the effective machine-level fix is forcing AMDGPU
TearFree on for the active output.
