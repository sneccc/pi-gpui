//! macOS half of the automation: AppKit mouse events, CoreGraphics capture.

use std::ffi::c_void;
use std::path::Path;

use gpui::{AnyWindowHandle, AsyncApp, Window};
use objc::runtime::Object;
use objc::{class, msg_send, sel, sel_impl};
use raw_window_handle::{HasWindowHandle, RawWindowHandle};

use super::MouseEvent;

/// gpui's NSView of the window, the window's number and its height in points.
#[derive(Clone, Copy)]
pub struct NativeWindow {
    view: *mut Object,
    number: u32,
    height: f64,
}

#[repr(C)]
struct NSPoint {
    x: f64,
    y: f64,
}

/// Hands gpui's view a mouse event at (`x`, `y`) from the window's top left
/// corner, as AppKit would. The event never passes through the system, so the
/// real pointer stays where it is.
unsafe fn send_mouse(target: NativeWindow, event: MouseEvent, x: f64, y: f64) {
    // NSEventTypeMouseMoved, NSEventTypeLeftMouseDown, NSEventTypeLeftMouseUp,
    // NSEventTypeRightMouseDown, NSEventTypeRightMouseUp.
    let (kind, clicks): (usize, isize) = match event {
        MouseEvent::Move => (5, 0),
        MouseEvent::Down => (1, 1),
        MouseEvent::Up => (2, 1),
        MouseEvent::RightDown => (3, 1),
        MouseEvent::RightUp => (4, 1),
    };
    // AppKit counts from the bottom left corner.
    let location = NSPoint {
        x,
        y: target.height - y,
    };
    unsafe {
        let process: *mut Object = msg_send![class!(NSProcessInfo), processInfo];
        let timestamp: f64 = msg_send![process, systemUptime];
        let nil: *mut Object = std::ptr::null_mut();
        let native: *mut Object = msg_send![class!(NSEvent),
            mouseEventWithType: kind
            location: location
            modifierFlags: 0usize
            timestamp: timestamp
            windowNumber: target.number as isize
            context: nil
            eventNumber: 0isize
            clickCount: clicks
            pressure: 1.0f32];
        if native.is_null() {
            return;
        }
        let _: () = match event {
            MouseEvent::Move => msg_send![target.view, mouseMoved: native],
            MouseEvent::Down => msg_send![target.view, mouseDown: native],
            MouseEvent::Up => msg_send![target.view, mouseUp: native],
            MouseEvent::RightDown => msg_send![target.view, rightMouseDown: native],
            MouseEvent::RightUp => msg_send![target.view, rightMouseUp: native],
        };
    }
}

pub fn native_window(window: &Window) -> Option<NativeWindow> {
    let handle = HasWindowHandle::window_handle(window).ok()?;
    let RawWindowHandle::AppKit(appkit) = handle.as_raw() else {
        return None;
    };
    let view = appkit.ns_view.as_ptr() as *mut Object;
    // SAFETY: `view` is gpui's live NSView for this window, on the main thread.
    unsafe {
        let ns_window: *mut Object = msg_send![view, window];
        if ns_window.is_null() {
            return None;
        }
        let number: isize = msg_send![ns_window, windowNumber];
        Some(NativeWindow {
            view,
            number: u32::try_from(number).ok()?,
            height: f64::from(f32::from(window.viewport_size().height)),
        })
    }
}

#[repr(C)]
struct CGRect {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

#[link(name = "CoreGraphics", kind = "framework")]
unsafe extern "C" {
    fn CGWindowListCreateImage(
        bounds: CGRect,
        list_option: u32,
        window: u32,
        image_option: u32,
    ) -> *mut c_void;
    fn CGImageRelease(image: *mut c_void);
}

const WINDOW_LIST_INCLUDING_WINDOW: u32 = 1 << 3;
const IMAGE_BOUNDS_IGNORE_FRAMING: u32 = 1 << 0;
const IMAGE_NOMINAL_RESOLUTION: u32 = 1 << 4;
const BITMAP_FILE_TYPE_PNG: usize = 4;

/// Draws a frame even if the window is hidden behind others (gpui stops its
/// display link then).
pub unsafe fn draw(target: NativeWindow) {
    unsafe {
        let layer: *mut Object = msg_send![target.view, layer];
        let _: () = msg_send![target.view, displayLayer: layer];
    }
}

/// Writes the window's picture to `path`.
pub unsafe fn capture(
    target: NativeWindow,
    path: &Path,
    full_resolution: bool,
) -> Result<(), String> {
    unsafe {
        // CGRectNull: the bounds of the window itself.
        let bounds = CGRect {
            x: f64::INFINITY,
            y: f64::INFINITY,
            width: 0.,
            height: 0.,
        };
        let mut image_option = IMAGE_BOUNDS_IGNORE_FRAMING;
        if !full_resolution {
            image_option |= IMAGE_NOMINAL_RESOLUTION;
        }
        let image = CGWindowListCreateImage(
            bounds,
            WINDOW_LIST_INCLUDING_WINDOW,
            target.number,
            image_option,
        );
        if image.is_null() {
            return Err("the window could not be captured".into());
        }
        let bitmap: *mut Object = msg_send![class!(NSBitmapImageRep), alloc];
        let bitmap: *mut Object = msg_send![bitmap, initWithCGImage: image];
        CGImageRelease(image);
        let properties: *mut Object = msg_send![class!(NSDictionary), dictionary];
        let data: *mut Object =
            msg_send![bitmap, representationUsingType: BITMAP_FILE_TYPE_PNG properties: properties];
        let written = if data.is_null() {
            Err("the picture could not be encoded".to_string())
        } else {
            let bytes: *const u8 = msg_send![data, bytes];
            let length: usize = msg_send![data, length];
            std::fs::write(path, std::slice::from_raw_parts(bytes, length))
                .map_err(|error| error.to_string())
        };
        let _: () = msg_send![bitmap, release];
        written
    }
}

/// Sends a mouse event to the window's view.
pub fn mouse(
    _window: AnyWindowHandle,
    _cx: &mut AsyncApp,
    target: NativeWindow,
    event: MouseEvent,
    x: f64,
    y: f64,
) -> Result<(), String> {
    // SAFETY: as in `settle`.
    unsafe { send_mouse(target, event, x, y) };
    Ok(())
}
