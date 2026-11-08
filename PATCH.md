# gpui-patched

A local fork of [`gpui` 0.2.2](https://crates.io/crates/gpui/0.2.2) that removes
its unconditional per-vblank redraw loop on Windows.

## Why

Upstream `Platform::run()` on Windows spawns a `VSyncProvider` thread that
loops forever:

```rust
loop {
    vsync_provider.wait_for_vsync();   // DwmFlush()
    if check_device_lost(...) { ... }
    let Some(all_windows) = all_windows.upgrade() else { break };
    for hwnd in all_windows.read().iter() {
        unsafe { RedrawWindow(Some(hwnd.as_raw()), None, None, RDW_INVALIDATE); }
    }
}
```

Nothing gates that loop. It runs whether or not any window is dirty, whether
or not the window is visible, and whether or not the window is minimised. A
window sitting still and fully occluded still wakes the CPU every vblank.

Measured on an 8-core Xeon E3-1275 v3, Windows 10 19045, 40 s idle window
after warm-up:

| | upstream 0.2.2 | this fork |
| --- | --- | --- |
| idle CPU (empty 1100x700 window) | 0.77 % of one core | 0.000 % |
| working set | 27.0 MB | 27.2 MB |
| private bytes | 35.8 MB | 36.3 MB |
| release binary | 3.29 MB | 3.29 MB |

Minimising the upstream window makes it worse, not better: the thread keeps
flushing and the window messages that used to share the time slice stop
arriving.

## What changed

Three files, roughly 60 lines.

### `src/platform/windows/platform.rs`

A `RedrawRequests` struct: a `Mutex<bool>` plus a `Condvar`. The vsync thread
parks on `wait_for_request()` and only enters a vblank when something asked
for a frame:

```rust
if !redraw.wait_for_request() {
    break;
}
vsync_provider.wait_for_vsync();
// ... device-lost check, then RedrawWindow for each window
```

The pending flag is cleared *before* the draw rather than after, so a request
that arrives mid-frame is not swallowed — it is observed on the next wait.

`PlatformWindowCreateContext` and `WindowCreationInfo` carry an
`Arc<RedrawRequests>` down to the window layer.

### `src/platform.rs`

`PlatformWindow` gains two methods, both with defaults that keep macOS and
Linux untouched:

```rust
fn request_redraw(&self) {}
fn downgrade_redraw_handle(&self) -> Option<std::rc::Weak<dyn Fn()>> { None }
```

### `src/window.rs`

`WindowInvalidator` holds an `on_dirty` callback. Both `set_dirty(true)` and
`invalidate_view` invoke it — **after** the dirty flag is set, never before:

```rust
if inner.draw_phase == DrawPhase::None {
    inner.dirty = true;
    let wake = inner.on_dirty.clone();
    drop(inner);
    cx.push_effect(Effect::Notify { emitter: entity });
    // Wake only after the dirty flag is visible to the frame producer, or the
    // producer can wake first, see a clean window, and go back to sleep with
    // the redraw still pending.
    if let Some(wake) = wake { wake(); }
    true
}
```

The ordering matters and was a real bug during development: waking first let
the producer run, observe `is_dirty() == false`, present nothing, and park
again with the redraw still owed.

## Verifying it still draws

An idle CPU of 0.00 % is indistinguishable from a dead window by measurement
alone, so the fork is checked with a probe that counts frames and logs the
wake path. On window resize the log shows the full chain:

```
set_dirty(true) has_cb=true waking=true
vsync woke
vsync after DwmFlush
RedrawWindow ok=true
```

The window reports `Responding = True` throughout and repaints on resize.

Note that `Context::notify()` does **not** mark a window dirty — it only tells
observers an entity changed. A probe that calls `notify()` and expects a
repaint will measure nothing. Use `Window::refresh()` or
`App::refresh_windows()`.

## Using it

```toml
[dependencies]
gpui = { git = "https://github.com/<owner>/gpui-patched", tag = "v0.2.2-patched" }
```

Or as a patch on top of a normal dependency:

```toml
[patch.crates-io]
gpui = { git = "https://github.com/<owner>/gpui-patched" }
```

## Maintenance

Upstream is not accepting general-purpose changes: Zed has said that in 2026
GPUI work is limited to what Zed itself needs. The community fork
[`gpui-ce`](https://crates.io/crates/gpui-ce) carries the rest.

This fork tracks `gpui` 0.2.2. When bumping the upstream version, re-apply the
three changes above and re-run the measurements — in particular confirm the
idle CPU figure, since that is the entire reason the fork exists.

## Licence

Apache-2.0, same as upstream. Original work by Zed Industries.
