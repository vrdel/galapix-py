use anyhow::{anyhow, Result};
use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::path::Path;
use std::ptr;
use std::slice;
use std::sync::OnceLock;

const VIPS_ACCESS_RANDOM: c_int = 0;
const VIPS_ACCESS_SEQUENTIAL: c_int = 1;
const VIPS_SIZE_FORCE: c_int = 3;

const ACCESS_KEY: &[u8] = b"access\0";
const HEIGHT_KEY: &[u8] = b"height\0";
const Q_KEY: &[u8] = b"Q\0";
const SIZE_KEY: &[u8] = b"size\0";
const STRIP_KEY: &[u8] = b"strip\0";

#[repr(C)]
struct VipsImage {
    _private: [u8; 0],
}

#[link(name = "vips")]
#[link(name = "gobject-2.0")]
#[link(name = "glib-2.0")]
extern "C" {
    fn vips_init(argv0: *const c_char) -> c_int;
    fn vips_shutdown();
    fn vips_thread_shutdown();
    fn vips_error_buffer() -> *const c_char;
    fn vips_error_clear();
    fn vips_concurrency_set(concurrency: c_int);
    fn vips_image_new_from_file(name: *const c_char, ...) -> *mut VipsImage;
    fn vips_image_get_width(image: *const VipsImage) -> c_int;
    fn vips_image_get_height(image: *const VipsImage) -> c_int;
    fn vips_thumbnail_image(
        input: *mut VipsImage,
        output: *mut *mut VipsImage,
        width: c_int,
        ...
    ) -> c_int;
    fn vips_extract_area(
        input: *mut VipsImage,
        output: *mut *mut VipsImage,
        left: c_int,
        top: c_int,
        width: c_int,
        height: c_int,
        ...
    ) -> c_int;
    fn vips_jpegsave_buffer(
        input: *mut VipsImage,
        buf: *mut *mut c_void,
        len: *mut usize,
        ...
    ) -> c_int;
    fn g_object_unref(object: *mut c_void);
    fn g_free(mem: *mut c_void);
}

static VIPS_RUNTIME: OnceLock<VipsRuntime> = OnceLock::new();

pub fn initialize(app_name: &str, outer_threads: usize) -> Result<()> {
    if VIPS_RUNTIME.get().is_some() {
        return Ok(());
    }
    let runtime = VipsRuntime::new(app_name, outer_threads)?;
    let _ = VIPS_RUNTIME.set(runtime);
    Ok(())
}

pub fn shutdown_thread() {
    unsafe {
        vips_thread_shutdown();
    }
}

struct VipsRuntime;

impl VipsRuntime {
    fn new(app_name: &str, outer_threads: usize) -> Result<Self> {
        let app_name = CString::new(app_name)?;
        let status = unsafe { vips_init(app_name.as_ptr()) };
        if status != 0 {
            return Err(last_error("vips_init"));
        }
        unsafe {
            vips_concurrency_set(outer_threads.max(1) as c_int);
        }
        Ok(Self)
    }
}

impl Drop for VipsRuntime {
    fn drop(&mut self) {
        unsafe {
            vips_shutdown();
        }
    }
}

pub struct Image {
    raw: *mut VipsImage,
}

impl Image {
    pub fn open_random(path: &Path) -> Result<Self> {
        Self::open_with_access(path, VIPS_ACCESS_RANDOM)
    }

    pub fn open_sequential(path: &Path) -> Result<Self> {
        Self::open_with_access(path, VIPS_ACCESS_SEQUENTIAL)
    }

    fn open_with_access(path: &Path, access: c_int) -> Result<Self> {
        let path = CString::new(path.to_string_lossy().as_bytes())?;
        let raw = unsafe {
            vips_image_new_from_file(
                path.as_ptr(),
                ACCESS_KEY.as_ptr() as *const c_char,
                access,
                ptr::null::<c_char>(),
            )
        };
        if raw.is_null() {
            return Err(last_error("vips_image_new_from_file"));
        }
        Ok(Self { raw })
    }

    pub fn width(&self) -> i32 {
        unsafe { vips_image_get_width(self.raw) }
    }

    pub fn height(&self) -> i32 {
        unsafe { vips_image_get_height(self.raw) }
    }

    pub fn thumbnail_force(&self, width: i32, height: i32) -> Result<Self> {
        let mut output = ptr::null_mut();
        let status = unsafe {
            vips_thumbnail_image(
                self.raw,
                &mut output,
                width,
                HEIGHT_KEY.as_ptr() as *const c_char,
                height,
                SIZE_KEY.as_ptr() as *const c_char,
                VIPS_SIZE_FORCE,
                ptr::null::<c_char>(),
            )
        };
        if status != 0 || output.is_null() {
            return Err(last_error("vips_thumbnail_image"));
        }
        Ok(Self { raw: output })
    }

    pub fn crop(&self, left: i32, top: i32, width: i32, height: i32) -> Result<Self> {
        let mut output = ptr::null_mut();
        let status = unsafe {
            vips_extract_area(
                self.raw,
                &mut output,
                left,
                top,
                width,
                height,
                ptr::null::<c_char>(),
            )
        };
        if status != 0 || output.is_null() {
            return Err(last_error("vips_extract_area"));
        }
        Ok(Self { raw: output })
    }

    pub fn save_jpeg(&self, quality: i32) -> Result<Vec<u8>> {
        let mut buf = ptr::null_mut();
        let mut len = 0usize;
        let status = unsafe {
            vips_jpegsave_buffer(
                self.raw,
                &mut buf,
                &mut len,
                Q_KEY.as_ptr() as *const c_char,
                quality,
                STRIP_KEY.as_ptr() as *const c_char,
                1,
                ptr::null::<c_char>(),
            )
        };
        if status != 0 || buf.is_null() {
            return Err(last_error("vips_jpegsave_buffer"));
        }

        let bytes = unsafe { slice::from_raw_parts(buf as *const u8, len).to_vec() };
        unsafe {
            g_free(buf);
        }
        Ok(bytes)
    }
}

impl Drop for Image {
    fn drop(&mut self) {
        unsafe {
            g_object_unref(self.raw.cast());
        }
    }
}

fn last_error(operation: &str) -> anyhow::Error {
    let message = unsafe {
        let ptr = vips_error_buffer();
        let text = if ptr.is_null() {
            "unknown libvips error".to_string()
        } else {
            CStr::from_ptr(ptr).to_string_lossy().trim().to_string()
        };
        vips_error_clear();
        text
    };
    anyhow!("{operation} failed: {message}")
}
