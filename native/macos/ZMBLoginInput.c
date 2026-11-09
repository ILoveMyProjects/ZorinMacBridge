#include <CoreFoundation/CoreFoundation.h>
#include <ApplicationServices/ApplicationServices.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/hid/IOHIDKeys.h>
#include <dlfcn.h>
#include <IOKit/hidsystem/IOHIDLib.h>
#include <IOKit/hidsystem/IOHIDParameter.h>
#include <IOKit/hidsystem/IOLLEvent.h>
#include <IOKit/hidsystem/ev_keymap.h>
#include <mach/mach.h>
#include <pthread.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>

/*
 * LoginWindow input bridge.
 *
 * Backend order after CoreHID has already failed in the Swift layer:
 *   1. IOHIDUserDevice virtual keyboard/pointer (real HID stack)
 *   2. IOHIDSystem / IOHIDPostEvent
 *   3. kCGSessionEventTap as last diagnostic fallback
 *
 * The user-device backend is intentionally preferred over Quartz because
 * LoginWindow can ignore synthetic CGEvent input while still accepting input
 * from a virtual HID device presented to the kernel HID stack.
 */

enum {
    ZMB_LOGIN_INPUT_NONE = 0,
    ZMB_LOGIN_INPUT_USER_DEVICE = 1,
    ZMB_LOGIN_INPUT_IOHID_SYSTEM = 2,
    ZMB_LOGIN_INPUT_QUARTZ = 3,
};

/*
 * Xcode 26.6 no longer ships IOKit/hid/IOHIDUserDevice.h in the public
 * macOS SDK even though Apple still documents the opaque reference type.
 * Keep this backend optional: resolve the two runtime symbols from IOKit
 * dynamically so the whole ZMB native dylib still builds when the SDK header
 * is absent.  If the running OS does not export them, the reboot matrix sees
 * FAIL_OPEN for this backend and continues to the other native candidates.
 */
typedef struct __IOHIDUserDevice *IOHIDUserDeviceRef;
typedef IOHIDUserDeviceRef (*ZMBIOHIDUserDeviceCreateFn)(CFAllocatorRef, CFDictionaryRef);
typedef IOReturn (*ZMBIOHIDUserDeviceHandleReportFn)(IOHIDUserDeviceRef, uint8_t *, CFIndex);

static io_connect_t g_zmb_hid = IO_OBJECT_NULL;
static void *g_zmb_iokit_handle = NULL;
static ZMBIOHIDUserDeviceCreateFn g_zmb_user_device_create = NULL;
static ZMBIOHIDUserDeviceHandleReportFn g_zmb_user_device_handle_report = NULL;
static IOHIDUserDeviceRef g_zmb_user_keyboard = NULL;
static IOHIDUserDeviceRef g_zmb_user_pointer = NULL;
static uint32_t g_zmb_hid_refs = 0;
static int32_t g_zmb_backend = ZMB_LOGIN_INPUT_NONE;
static pthread_mutex_t g_zmb_hid_lock = PTHREAD_MUTEX_INITIALIZER;

static uint8_t g_zmb_keyboard_modifiers = 0;
static uint8_t g_zmb_keyboard_keys[6] = {0};
static uint16_t g_zmb_pointer_x = 16384;
static uint16_t g_zmb_pointer_y = 16384;
static uint8_t g_zmb_pointer_buttons = 0;

static const uint8_t kZMBKeyboardDescriptor[] = {
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01,
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7,
    0x15, 0x00, 0x25, 0x01, 0x75, 0x01,
    0x95, 0x08, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01,
    0x95, 0x05, 0x75, 0x01, 0x05, 0x08,
    0x19, 0x01, 0x29, 0x05, 0x91, 0x02,
    0x95, 0x01, 0x75, 0x03, 0x91, 0x01,
    0x95, 0x06, 0x75, 0x08, 0x15, 0x00,
    0x25, 0x65, 0x05, 0x07, 0x19, 0x00,
    0x29, 0x65, 0x81, 0x00, 0xC0
};

static const uint8_t kZMBPointerDescriptor[] = {
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01,
    0x09, 0x01, 0xA1, 0x00,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x03,
    0x15, 0x00, 0x25, 0x01, 0x95, 0x03,
    0x75, 0x01, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x05, 0x81, 0x01,
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31,
    0x16, 0x00, 0x00, 0x26, 0xFF, 0x7F,
    0x75, 0x10, 0x95, 0x02, 0x81, 0x02,
    0x09, 0x38, 0x15, 0x81, 0x25, 0x7F,
    0x75, 0x08, 0x95, 0x01, 0x81, 0x06,
    0xC0, 0xC0
};

static void zmb_dict_set_i32(CFMutableDictionaryRef dict, CFStringRef key, int32_t value) {
    CFNumberRef number = CFNumberCreate(kCFAllocatorDefault, kCFNumberSInt32Type, &value);
    if (number) {
        CFDictionarySetValue(dict, key, number);
        CFRelease(number);
    }
}

static int32_t zmb_load_user_device_api(void) {
    if (g_zmb_user_device_create && g_zmb_user_device_handle_report) {
        return (int32_t)kIOReturnSuccess;
    }
    if (!g_zmb_iokit_handle) {
        g_zmb_iokit_handle = dlopen(
            "/System/Library/Frameworks/IOKit.framework/IOKit",
            RTLD_LAZY | RTLD_LOCAL
        );
    }
    if (!g_zmb_iokit_handle) return (int32_t)kIOReturnUnsupported;

    g_zmb_user_device_create = (ZMBIOHIDUserDeviceCreateFn)dlsym(
        g_zmb_iokit_handle, "IOHIDUserDeviceCreate"
    );
    g_zmb_user_device_handle_report = (ZMBIOHIDUserDeviceHandleReportFn)dlsym(
        g_zmb_iokit_handle, "IOHIDUserDeviceHandleReport"
    );
    if (!g_zmb_user_device_create || !g_zmb_user_device_handle_report) {
        g_zmb_user_device_create = NULL;
        g_zmb_user_device_handle_report = NULL;
        return (int32_t)kIOReturnUnsupported;
    }
    return (int32_t)kIOReturnSuccess;
}

static IOHIDUserDeviceRef zmb_create_user_device(const uint8_t *descriptor,
                                                  CFIndex descriptor_length,
                                                  int32_t product_id,
                                                  int32_t primary_usage,
                                                  CFStringRef product_name) {
    CFMutableDictionaryRef properties = CFDictionaryCreateMutable(
        kCFAllocatorDefault,
        0,
        &kCFTypeDictionaryKeyCallBacks,
        &kCFTypeDictionaryValueCallBacks
    );
    if (!properties) return NULL;

    CFDataRef descriptor_data = CFDataCreate(kCFAllocatorDefault, descriptor, descriptor_length);
    if (!descriptor_data) {
        CFRelease(properties);
        return NULL;
    }

    CFDictionarySetValue(properties, CFSTR(kIOHIDReportDescriptorKey), descriptor_data);
    CFDictionarySetValue(properties, CFSTR(kIOHIDTransportKey), CFSTR("Virtual"));
    CFDictionarySetValue(properties, CFSTR(kIOHIDManufacturerKey), CFSTR("ZorinMacBridge"));
    CFDictionarySetValue(properties, CFSTR(kIOHIDProductKey), product_name);
    zmb_dict_set_i32(properties, CFSTR(kIOHIDVendorIDKey), 0x5A4D);
    zmb_dict_set_i32(properties, CFSTR(kIOHIDProductIDKey), product_id);
    zmb_dict_set_i32(properties, CFSTR(kIOHIDPrimaryUsagePageKey), 0x01);
    zmb_dict_set_i32(properties, CFSTR(kIOHIDPrimaryUsageKey), primary_usage);

    IOHIDUserDeviceRef device = g_zmb_user_device_create(kCFAllocatorDefault, properties);
    CFRelease(descriptor_data);
    CFRelease(properties);
    return device;
}

static void zmb_reset_user_device_state(void) {
    g_zmb_keyboard_modifiers = 0;
    memset(g_zmb_keyboard_keys, 0, sizeof(g_zmb_keyboard_keys));
    g_zmb_pointer_x = 16384;
    g_zmb_pointer_y = 16384;
    g_zmb_pointer_buttons = 0;
}

static int32_t zmb_open_user_devices(void) {
    int32_t api_rc = zmb_load_user_device_api();
    if (api_rc != (int32_t)kIOReturnSuccess) return api_rc;

    IOHIDUserDeviceRef keyboard = zmb_create_user_device(
        kZMBKeyboardDescriptor,
        (CFIndex)sizeof(kZMBKeyboardDescriptor),
        0x1001,
        0x06,
        CFSTR("ZorinMacBridge LoginWindow Keyboard")
    );
    if (!keyboard) return (int32_t)kIOReturnNotOpen;

    IOHIDUserDeviceRef pointer = zmb_create_user_device(
        kZMBPointerDescriptor,
        (CFIndex)sizeof(kZMBPointerDescriptor),
        0x1002,
        0x02,
        CFSTR("ZorinMacBridge LoginWindow Pointer")
    );
    if (!pointer) {
        CFRelease(keyboard);
        return (int32_t)kIOReturnNotOpen;
    }

    g_zmb_user_keyboard = keyboard;
    g_zmb_user_pointer = pointer;
    zmb_reset_user_device_state();
    /* Give IOHIDFamily a short bounded window to publish both device services. */
    usleep(120000);
    return (int32_t)kIOReturnSuccess;
}

static int32_t zmb_quartz_key(uint16_t key_code, int32_t down, uint32_t flags) {
    CGEventSourceRef source = CGEventSourceCreate(kCGEventSourceStateHIDSystemState);
    CGEventRef event = CGEventCreateKeyboardEvent(source, (CGKeyCode)key_code, down != 0);
    if (source) CFRelease(source);
    if (!event) return (int32_t)kIOReturnNoMemory;
    CGEventSetFlags(event, (CGEventFlags)flags);
    CGEventPost(kCGSessionEventTap, event);
    CFRelease(event);
    return (int32_t)kIOReturnSuccess;
}

static int32_t zmb_quartz_pointer(uint32_t type, int32_t x, int32_t y,
                                  int32_t button, uint32_t flags) {
    CGEventSourceRef source = CGEventSourceCreate(kCGEventSourceStateHIDSystemState);
    CGEventRef event = CGEventCreateMouseEvent(
        source,
        (CGEventType)type,
        CGPointMake((CGFloat)x, (CGFloat)y),
        (CGMouseButton)button
    );
    if (source) CFRelease(source);
    if (!event) return (int32_t)kIOReturnNoMemory;
    CGEventSetFlags(event, (CGEventFlags)flags);
    CGEventPost(kCGSessionEventTap, event);
    CFRelease(event);
    return (int32_t)kIOReturnSuccess;
}

static int32_t zmb_quartz_scroll(int32_t dx, int32_t dy, uint32_t flags) {
    CGEventSourceRef source = CGEventSourceCreate(kCGEventSourceStateHIDSystemState);
    CGEventRef event = CGEventCreateScrollWheelEvent(
        source,
        kCGScrollEventUnitLine,
        2,
        (int32_t)dy,
        (int32_t)dx
    );
    if (source) CFRelease(source);
    if (!event) return (int32_t)kIOReturnNoMemory;
    CGEventSetFlags(event, (CGEventFlags)flags);
    CGEventPost(kCGSessionEventTap, event);
    CFRelease(event);
    return (int32_t)kIOReturnSuccess;
}

static int32_t zmb_post(UInt32 type, IOGPoint location, NXEventData *data,
                        IOOptionBits flags, IOOptionBits options) {
    pthread_mutex_lock(&g_zmb_hid_lock);
    io_connect_t hid = g_zmb_hid;
    int32_t backend = g_zmb_backend;
    if (hid == IO_OBJECT_NULL) {
        pthread_mutex_unlock(&g_zmb_hid_lock);
        return backend == ZMB_LOGIN_INPUT_QUARTZ
            ? (int32_t)kIOReturnNotPrivileged
            : (int32_t)kIOReturnNotOpen;
    }
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    kern_return_t kr = IOHIDPostEvent(
        hid,
        type,
        location,
        data,
        kNXEventDataVersion,
        flags | NX_NONCOALSESCEDMASK,
        options
    );
#pragma clang diagnostic pop
    pthread_mutex_unlock(&g_zmb_hid_lock);
    return (int32_t)kr;
}

static void zmb_close_locked(void) {
    if (g_zmb_hid != IO_OBJECT_NULL) {
        IOServiceClose(g_zmb_hid);
        g_zmb_hid = IO_OBJECT_NULL;
    }
    if (g_zmb_user_keyboard) {
        CFRelease(g_zmb_user_keyboard);
        g_zmb_user_keyboard = NULL;
    }
    if (g_zmb_user_pointer) {
        CFRelease(g_zmb_user_pointer);
        g_zmb_user_pointer = NULL;
    }
    g_zmb_hid_refs = 0;
    g_zmb_backend = ZMB_LOGIN_INPUT_NONE;
    zmb_reset_user_device_state();
}

static int32_t zmb_open_iohid_system_locked(void) {
    io_service_t service = IOServiceGetMatchingService(
        kIOMainPortDefault,
        IOServiceMatching(kIOHIDSystemClass)
    );
    if (service == IO_OBJECT_NULL) return (int32_t)kIOReturnNotFound;

    io_connect_t connection = IO_OBJECT_NULL;
    kern_return_t kr = IOServiceOpen(
        service,
        mach_task_self(),
        kIOHIDParamConnectType,
        &connection
    );
    IOObjectRelease(service);
    if (kr != kIOReturnSuccess) return (int32_t)kr;

    g_zmb_hid = connection;
    return (int32_t)kIOReturnSuccess;
}

static int32_t zmb_open_backend_locked(int32_t backend) {
    int32_t rc = (int32_t)kIOReturnUnsupported;
    switch (backend) {
        case ZMB_LOGIN_INPUT_USER_DEVICE:
            rc = zmb_open_user_devices();
            break;
        case ZMB_LOGIN_INPUT_IOHID_SYSTEM:
            rc = zmb_open_iohid_system_locked();
            break;
        case ZMB_LOGIN_INPUT_QUARTZ:
            rc = (int32_t)kIOReturnSuccess;
            break;
        default:
            return (int32_t)kIOReturnBadArgument;
    }
    if (rc == (int32_t)kIOReturnSuccess) {
        g_zmb_hid_refs = 1;
        g_zmb_backend = backend;
    }
    return rc;
}

/*
 * Open one exact backend.  Unlike zmb_login_input_open(), this function never
 * silently falls through to another mechanism.  It exists so the reboot
 * LoginWindow matrix can prove every native path independently.
 */
int32_t zmb_login_input_open_backend(int32_t backend) {
    pthread_mutex_lock(&g_zmb_hid_lock);
    zmb_close_locked();
    int32_t rc = zmb_open_backend_locked(backend);
    pthread_mutex_unlock(&g_zmb_hid_lock);
    return rc;
}

int32_t zmb_login_input_open(void) {
    pthread_mutex_lock(&g_zmb_hid_lock);
    if (g_zmb_hid_refs > 0) {
        g_zmb_hid_refs += 1;
        pthread_mutex_unlock(&g_zmb_hid_lock);
        return (int32_t)kIOReturnSuccess;
    }

    const int32_t candidates[] = {
        ZMB_LOGIN_INPUT_USER_DEVICE,
        ZMB_LOGIN_INPUT_IOHID_SYSTEM,
        ZMB_LOGIN_INPUT_QUARTZ,
    };
    int32_t last_rc = (int32_t)kIOReturnNotOpen;
    for (size_t i = 0; i < sizeof(candidates) / sizeof(candidates[0]); ++i) {
        zmb_close_locked();
        last_rc = zmb_open_backend_locked(candidates[i]);
        if (last_rc == (int32_t)kIOReturnSuccess) {
            pthread_mutex_unlock(&g_zmb_hid_lock);
            return last_rc;
        }
    }
    zmb_close_locked();
    pthread_mutex_unlock(&g_zmb_hid_lock);
    return last_rc;
}

int32_t zmb_login_input_backend(void) {
    pthread_mutex_lock(&g_zmb_hid_lock);
    int32_t backend = g_zmb_backend;
    pthread_mutex_unlock(&g_zmb_hid_lock);
    return backend;
}

void zmb_login_input_close(void) {
    pthread_mutex_lock(&g_zmb_hid_lock);
    if (g_zmb_hid_refs > 1) {
        g_zmb_hid_refs -= 1;
    } else {
        zmb_close_locked();
    }
    pthread_mutex_unlock(&g_zmb_hid_lock);
}

int32_t zmb_login_input_hid_key(uint16_t usage, int32_t down) {
    pthread_mutex_lock(&g_zmb_hid_lock);
    if (g_zmb_backend != ZMB_LOGIN_INPUT_USER_DEVICE || !g_zmb_user_keyboard) {
        pthread_mutex_unlock(&g_zmb_hid_lock);
        return (int32_t)kIOReturnNotOpen;
    }

    uint8_t key = (uint8_t)(usage & 0xFFu);
    if (key >= 0xE0 && key <= 0xE7) {
        uint8_t bit = (uint8_t)(1u << (key - 0xE0));
        if (down) g_zmb_keyboard_modifiers |= bit;
        else g_zmb_keyboard_modifiers &= (uint8_t)~bit;
    } else if (down) {
        int present = 0;
        for (size_t i = 0; i < 6; ++i) {
            if (g_zmb_keyboard_keys[i] == key) {
                present = 1;
                break;
            }
        }
        if (!present) {
            size_t slot = 6;
            for (size_t i = 0; i < 6; ++i) {
                if (g_zmb_keyboard_keys[i] == 0) {
                    slot = i;
                    break;
                }
            }
            if (slot == 6) {
                pthread_mutex_unlock(&g_zmb_hid_lock);
                return (int32_t)kIOReturnNoResources;
            }
            g_zmb_keyboard_keys[slot] = key;
        }
    } else {
        for (size_t i = 0; i < 6; ++i) {
            if (g_zmb_keyboard_keys[i] == key) g_zmb_keyboard_keys[i] = 0;
        }
    }

    uint8_t report[8] = {0};
    report[0] = g_zmb_keyboard_modifiers;
    for (size_t i = 0; i < 6; ++i) report[2 + i] = g_zmb_keyboard_keys[i];
    IOReturn rc = g_zmb_user_device_handle_report(g_zmb_user_keyboard, report, (CFIndex)sizeof(report));
    pthread_mutex_unlock(&g_zmb_hid_lock);
    return (int32_t)rc;
}

int32_t zmb_login_input_hid_pointer(int32_t x, int32_t y, uint8_t buttons, int32_t wheel) {
    pthread_mutex_lock(&g_zmb_hid_lock);
    if (g_zmb_backend != ZMB_LOGIN_INPUT_USER_DEVICE || !g_zmb_user_pointer) {
        pthread_mutex_unlock(&g_zmb_hid_lock);
        return (int32_t)kIOReturnNotOpen;
    }

    if (x < 0) x = 0;
    if (x > 32767) x = 32767;
    if (y < 0) y = 0;
    if (y > 32767) y = 32767;
    if (wheel < -127) wheel = -127;
    if (wheel > 127) wheel = 127;
    g_zmb_pointer_x = (uint16_t)x;
    g_zmb_pointer_y = (uint16_t)y;
    g_zmb_pointer_buttons = (uint8_t)(buttons & 0x07u);

    uint8_t report[6] = {
        g_zmb_pointer_buttons,
        (uint8_t)(g_zmb_pointer_x & 0xFFu),
        (uint8_t)((g_zmb_pointer_x >> 8) & 0xFFu),
        (uint8_t)(g_zmb_pointer_y & 0xFFu),
        (uint8_t)((g_zmb_pointer_y >> 8) & 0xFFu),
        (uint8_t)(int8_t)wheel,
    };
    IOReturn rc = g_zmb_user_device_handle_report(g_zmb_user_pointer, report, (CFIndex)sizeof(report));
    pthread_mutex_unlock(&g_zmb_hid_lock);
    return (int32_t)rc;
}

int32_t zmb_login_input_key(uint16_t key_code, int32_t down,
                            uint32_t flags, int32_t modifier) {
    int32_t backend = zmb_login_input_backend();
    if (backend == ZMB_LOGIN_INPUT_QUARTZ) {
        return zmb_quartz_key(key_code, down, flags);
    }
    if (backend != ZMB_LOGIN_INPUT_IOHID_SYSTEM) {
        return (int32_t)kIOReturnNotOpen;
    }

    NXEventData data;
    memset(&data, 0, sizeof(data));
    data.key.repeat = 0;
    data.key.keyCode = key_code;
    data.key.origCharSet = NX_ASCIISET;
    data.key.charSet = NX_ASCIISET;
    UInt32 type = modifier ? NX_FLAGSCHANGED : (down ? NX_KEYDOWN : NX_KEYUP);
    IOOptionBits options = modifier ? kIOHIDSetGlobalEventFlags : 0;
    IOGPoint location = {0, 0};
    return zmb_post(type, location, &data, (IOOptionBits)flags, options);
}

int32_t zmb_login_input_pointer(uint32_t type, int32_t x, int32_t y,
                                int32_t button, uint32_t flags) {
    int32_t backend = zmb_login_input_backend();
    if (backend == ZMB_LOGIN_INPUT_QUARTZ) {
        return zmb_quartz_pointer(type, x, y, button, flags);
    }
    if (backend != ZMB_LOGIN_INPUT_IOHID_SYSTEM) {
        return (int32_t)kIOReturnNotOpen;
    }

    NXEventData data;
    memset(&data, 0, sizeof(data));
    data.mouse.buttonNumber = (short)button;
    if (type == NX_LMOUSEDOWN || type == NX_RMOUSEDOWN || type == NX_OMOUSEDOWN ||
        type == NX_LMOUSEDRAGGED || type == NX_RMOUSEDRAGGED || type == NX_OMOUSEDRAGGED) {
        data.mouse.pressure = 255;
    }
    IOGPoint location = {(SInt32)x, (SInt32)y};
    return zmb_post(type, location, &data, (IOOptionBits)flags, kIOHIDSetCursorPosition);
}

int32_t zmb_login_input_scroll(int32_t dx, int32_t dy, uint32_t flags) {
    int32_t backend = zmb_login_input_backend();
    if (backend == ZMB_LOGIN_INPUT_QUARTZ) {
        return zmb_quartz_scroll(dx, dy, flags);
    }
    if (backend != ZMB_LOGIN_INPUT_IOHID_SYSTEM) {
        return (int32_t)kIOReturnNotOpen;
    }

    NXEventData data;
    memset(&data, 0, sizeof(data));
    data.scrollWheel.deltaAxis1 = dy;
    data.scrollWheel.deltaAxis2 = dx;
    data.scrollWheel.pointDeltaAxis1 = dy;
    data.scrollWheel.pointDeltaAxis2 = dx;
    IOGPoint location = {0, 0};
    return zmb_post(NX_SCROLLWHEELMOVED, location, &data, (IOOptionBits)flags, 0);
}
