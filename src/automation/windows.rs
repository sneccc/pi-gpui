//! Windows half of the automation. Mouse events are posted to the window's
//! own message queue (the real pointer never moves) and the picture comes from
//! `PrintWindow`, which asks the window to paint itself, so it works while
//! other windows cover it. Plain FFI: no `windows` crate needed.

use std::ffi::c_void;
use std::path::Path;

use gpui::{AnyWindowHandle, AsyncApp, Window};
use raw_window_handle::{HasWindowHandle, RawWindowHandle};

use super::MouseEvent;

/// The window's HWND.
#[derive(Clone, Copy)]
pub struct NativeWindow {
    hwnd: isize,
}

pub fn native_window(window: &Window) -> Option<NativeWindow> {
    let handle = HasWindowHandle::window_handle(window).ok()?;
    let RawWindowHandle::Win32(win32) = handle.as_raw() else {
        return None;
    };
    Some(NativeWindow {
        hwnd: win32.hwnd.get(),
    })
}

/// Posts a mouse message at (`x`, `y`) from the window's top left corner, in
/// points. It goes to the window's own message queue, so gpui handles it like
/// real input while the real pointer stays where it is.
pub fn mouse(
    _window: AnyWindowHandle,
    _cx: &mut AsyncApp,
    target: NativeWindow,
    event: MouseEvent,
    x: f64,
    y: f64,
) -> Result<(), String> {
    const WM_MOUSEMOVE: u32 = 0x0200;
    const WM_LBUTTONDOWN: u32 = 0x0201;
    const WM_LBUTTONUP: u32 = 0x0202;
    const WM_RBUTTONDOWN: u32 = 0x0204;
    const WM_RBUTTONUP: u32 = 0x0205;
    const MK_LBUTTON: usize = 0x0001;
    const MK_RBUTTON: usize = 0x0002;

    // SAFETY: a query on a live HWND.
    let scale = f64::from(unsafe { GetDpiForWindow(target.hwnd) }.max(96)) / 96.0;
    let position = mouse_lparam(x, y, scale);
    let (message, buttons) = match event {
        MouseEvent::Move => (WM_MOUSEMOVE, 0),
        MouseEvent::Down => (WM_LBUTTONDOWN, MK_LBUTTON),
        MouseEvent::Up => (WM_LBUTTONUP, 0),
        MouseEvent::RightDown => (WM_RBUTTONDOWN, MK_RBUTTON),
        MouseEvent::RightUp => (WM_RBUTTONUP, 0),
    };
    // SAFETY: posting to a live HWND from the thread that owns it.
    if unsafe { PostMessageW(target.hwnd, message, buttons, position) } == 0 {
        return Err("the mouse message could not be posted".into());
    }
    Ok(())
}

/// Client coordinates in device pixels (`scale` is dpi / 96), packed as
/// MAKELPARAM does: x in the low word and y in the high word, both signed.
fn mouse_lparam(x: f64, y: f64, scale: f64) -> isize {
    let (x, y) = ((x * scale).round() as i32, (y * scale).round() as i32);
    ((y as u16 as isize) << 16) | (x as u16 as isize)
}

/// The client area of a captured window (`client` is its left, top, width and
/// height inside the window rectangle), as opaque RGBA. `pixels` are the
/// window's BGRA rows, top first. Returns the size too.
fn crop_to_rgba(
    pixels: &[u8],
    window_width: usize,
    window_height: usize,
    client: (i32, i32, i32, i32),
) -> Result<(Vec<u8>, usize, usize), String> {
    let (left, top, width, height) = client;
    let (left, top) = (left.max(0) as usize, top.max(0) as usize);
    if left >= window_width || top >= window_height {
        return Err("the client area lies outside the window".into());
    }
    if pixels.len() < window_width * window_height * 4 {
        return Err("the window picture is too short".into());
    }
    let width = (width.max(1) as usize).min(window_width - left);
    let height = (height.max(1) as usize).min(window_height - top);
    let stride = window_width * 4;
    let mut rgba = Vec::with_capacity(width * height * 4);
    for row in top..top + height {
        let line = &pixels[row * stride + left * 4..row * stride + (left + width) * 4];
        for bgra in line.chunks_exact(4) {
            rgba.extend_from_slice(&[bgra[2], bgra[1], bgra[0], 255]);
        }
    }
    Ok((rgba, width, height))
}

/// A size in device pixels as points: 1 px per point at 96 dpi.
fn points(pixels: usize, dpi: u32) -> u32 {
    let scale = 96.0 / f64::from(dpi.max(96));
    ((pixels as f64 * scale).round() as u32).max(1)
}

/// gpui renders on its own schedule on Windows; nothing to force.
pub unsafe fn draw(_target: NativeWindow) {}

#[repr(C)]
#[derive(Default)]
struct Rect {
    left: i32,
    top: i32,
    right: i32,
    bottom: i32,
}

#[repr(C)]
#[derive(Default)]
struct Point {
    x: i32,
    y: i32,
}

#[repr(C)]
struct BitmapInfoHeader {
    size: u32,
    width: i32,
    height: i32,
    planes: u16,
    bit_count: u16,
    compression: u32,
    size_image: u32,
    x_pels_per_meter: i32,
    y_pels_per_meter: i32,
    colors_used: u32,
    colors_important: u32,
}

#[link(name = "user32")]
unsafe extern "system" {
    fn GetWindowRect(hwnd: isize, rect: *mut Rect) -> i32;
    fn ClientToScreen(hwnd: isize, point: *mut Point) -> i32;
    fn GetClientRect(hwnd: isize, rect: *mut Rect) -> i32;
    fn GetDC(hwnd: isize) -> isize;
    fn ReleaseDC(hwnd: isize, dc: isize) -> i32;
    fn PrintWindow(hwnd: isize, dc: isize, flags: u32) -> i32;
    fn GetDpiForWindow(hwnd: isize) -> u32;
    fn PostMessageW(hwnd: isize, message: u32, wparam: usize, lparam: isize) -> i32;
}

#[link(name = "gdi32")]
unsafe extern "system" {
    fn CreateCompatibleDC(dc: isize) -> isize;
    fn CreateCompatibleBitmap(dc: isize, width: i32, height: i32) -> isize;
    fn SelectObject(dc: isize, object: isize) -> isize;
    fn DeleteObject(object: isize) -> i32;
    fn DeleteDC(dc: isize) -> i32;
    fn GetDIBits(
        dc: isize,
        bitmap: isize,
        start: u32,
        lines: u32,
        bits: *mut c_void,
        info: *mut BitmapInfoHeader,
        usage: u32,
    ) -> i32;
}

const PW_RENDERFULLCONTENT: u32 = 2;
const BI_RGB: u32 = 0;
const DIB_RGB_COLORS: u32 = 0;

/// Writes the window's client area to `path`: 1 px per point, or one per
/// device pixel with `full_resolution`.
pub unsafe fn capture(
    target: NativeWindow,
    path: &Path,
    full_resolution: bool,
) -> Result<(), String> {
    let hwnd = target.hwnd;
    // SAFETY: plain GDI calls on a live HWND; every handle made here is
    // released before returning.
    let (pixels, window_width, window_height, client) = unsafe {
        let mut window_rect = Rect::default();
        let mut client_rect = Rect::default();
        let mut origin = Point::default();
        if GetWindowRect(hwnd, &mut window_rect) == 0
            || GetClientRect(hwnd, &mut client_rect) == 0
            || ClientToScreen(hwnd, &mut origin) == 0
        {
            return Err("the window has no size".into());
        }
        let width = window_rect.right - window_rect.left;
        let height = window_rect.bottom - window_rect.top;
        if width <= 0 || height <= 0 {
            return Err("the window is minimized".into());
        }

        let screen = GetDC(0);
        let memory = CreateCompatibleDC(screen);
        let bitmap = CreateCompatibleBitmap(screen, width, height);
        let previous = SelectObject(memory, bitmap);
        let painted = PrintWindow(hwnd, memory, PW_RENDERFULLCONTENT);

        let mut header = BitmapInfoHeader {
            size: size_of::<BitmapInfoHeader>() as u32,
            width,
            height: -height, // top-down
            planes: 1,
            bit_count: 32,
            compression: BI_RGB,
            size_image: 0,
            x_pels_per_meter: 0,
            y_pels_per_meter: 0,
            colors_used: 0,
            colors_important: 0,
        };
        let mut pixels = vec![0u8; (width * height * 4) as usize];
        let lines = GetDIBits(
            memory,
            bitmap,
            0,
            height as u32,
            pixels.as_mut_ptr().cast(),
            &mut header,
            DIB_RGB_COLORS,
        );

        SelectObject(memory, previous);
        DeleteObject(bitmap);
        DeleteDC(memory);
        ReleaseDC(0, screen);

        if painted == 0 || lines == 0 {
            return Err("the window could not be captured".into());
        }
        let client = (
            origin.x - window_rect.left,
            origin.y - window_rect.top,
            client_rect.right - client_rect.left,
            client_rect.bottom - client_rect.top,
        );
        (pixels, width, height, client)
    };

    // Crop to the client area (no frame, no shadow), BGRA to opaque RGBA.
    let (rgba, client_width, client_height) = crop_to_rgba(
        &pixels,
        window_width as usize,
        window_height as usize,
        client,
    )?;
    let mut picture = image::RgbaImage::from_raw(client_width as u32, client_height as u32, rgba)
        .ok_or("the picture has the wrong size")?;

    if !full_resolution {
        // SAFETY: a query on a live HWND.
        let dpi = unsafe { GetDpiForWindow(hwnd) }.max(96);
        if dpi != 96 {
            picture = image::imageops::resize(
                &picture,
                points(client_width, dpi),
                points(client_height, dpi),
                image::imageops::FilterType::Triangle,
            );
        }
    }
    picture.save(path).map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn low(lparam: isize) -> i16 {
        lparam as u16 as i16
    }
    fn high(lparam: isize) -> i16 {
        (lparam >> 16) as u16 as i16
    }

    #[test]
    fn mouse_position_is_packed_like_makelparam() {
        let lparam = mouse_lparam(100.0, 232.0, 1.0);
        assert_eq!((low(lparam), high(lparam)), (100, 232));
        assert_eq!(lparam, (232 << 16) | 100);
    }

    #[test]
    fn mouse_position_scales_points_to_device_pixels() {
        // 150 % scaling: 100 points are 150 device pixels.
        let lparam = mouse_lparam(100.0, 40.0, 1.5);
        assert_eq!((low(lparam), high(lparam)), (150, 60));
        // Rounds, as the window's own mapping back does.
        let lparam = mouse_lparam(10.4, 10.6, 1.0);
        assert_eq!((low(lparam), high(lparam)), (10, 11));
    }

    #[test]
    fn mouse_position_keeps_negative_coordinates_signed() {
        // GET_X_LPARAM / GET_Y_LPARAM read signed words: a point above or
        // left of the window must not turn into a huge positive one.
        let lparam = mouse_lparam(-5.0, -1.0, 1.0);
        assert_eq!((low(lparam), high(lparam)), (-5, -1));
    }

    /// A window of `width` x `height` BGRA pixels; pixel (x, y) is
    /// blue = x, green = y, red = 7.
    fn gradient(width: usize, height: usize) -> Vec<u8> {
        let mut pixels = Vec::new();
        for y in 0..height {
            for x in 0..width {
                pixels.extend_from_slice(&[x as u8, y as u8, 7, 0]);
            }
        }
        pixels
    }

    #[test]
    fn crop_takes_the_client_area_and_swaps_bgra_to_opaque_rgba() {
        let pixels = gradient(10, 8);
        let (rgba, width, height) = crop_to_rgba(&pixels, 10, 8, (2, 3, 4, 2)).unwrap();
        assert_eq!((width, height), (4, 2));
        assert_eq!(rgba.len(), 4 * 2 * 4);
        // First pixel is the window's (2, 3): R = 7, G = 3, B = 2, alpha forced.
        assert_eq!(&rgba[..4], &[7, 3, 2, 255]);
        // Last pixel is the window's (5, 4).
        assert_eq!(&rgba[rgba.len() - 4..], &[7, 4, 5, 255]);
        // The source alpha byte (0 here) never leaks through.
        assert!(rgba.chunks_exact(4).all(|pixel| pixel[3] == 255));
    }

    #[test]
    fn crop_clamps_a_client_area_that_overhangs_the_window() {
        let pixels = gradient(10, 8);
        let (_, width, height) = crop_to_rgba(&pixels, 10, 8, (8, 6, 50, 50)).unwrap();
        assert_eq!((width, height), (2, 2));
        // A zero-sized client area still gives a 1 x 1 picture.
        let (_, width, height) = crop_to_rgba(&pixels, 10, 8, (0, 0, 0, 0)).unwrap();
        assert_eq!((width, height), (1, 1));
        // A negative offset (the frame is outside the window) counts as 0.
        let (rgba, _, _) = crop_to_rgba(&pixels, 10, 8, (-4, -4, 2, 2)).unwrap();
        assert_eq!(&rgba[..4], &[7, 0, 0, 255]);
    }

    #[test]
    fn crop_reports_a_client_area_outside_the_window_instead_of_panicking() {
        let pixels = gradient(10, 8);
        assert!(crop_to_rgba(&pixels, 10, 8, (10, 0, 4, 4)).is_err());
        assert!(crop_to_rgba(&pixels, 10, 8, (0, 8, 4, 4)).is_err());
        assert!(crop_to_rgba(&pixels[..10], 10, 8, (0, 0, 4, 4)).is_err());
    }

    #[test]
    fn points_are_device_pixels_divided_by_the_scale() {
        assert_eq!(points(1180, 96), 1180);
        assert_eq!(points(1500, 144), 1000); // 150 %
        assert_eq!(points(1000, 192), 500); // 200 %
        assert_eq!(points(1, 192), 1); // never zero
        assert_eq!(points(100, 0), 100); // a failed dpi query counts as 96
    }
}
