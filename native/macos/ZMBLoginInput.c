#include <CoreFoundation/CoreFoundation.h>
#include <ApplicationServices/ApplicationServices.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/hidsystem/IOHIDLib.h>
#include <IOKit/hidsystem/IOHIDParameter.h>
#include <IOKit/hidsystem/IOLLEvent.h>
#include <IOKit/hidsystem/ev_keymap.h>
#include <mach/mach.h>
#include <pthread.h>
#include <stdint.h>
#include <string.h>

/*
 * LoginWindow input bridge.
 *
 * Quartz CGEvent injection is intentionally not used here: macOS can discard
 * CGEvent keyboard/click events while loginwindow owns SecureEventInput.  This
 * bridge talks to IOHIDSystem instead.  The exported ABI is deliberately tiny
 * and is loaded from the already-bundled libzmb_streamer.dylib.
 */

static io_connect_t g_zmb_hid = IO_OBJECT_NULL;
static uint32_t g_zmb_hid_refs = 0;
static pthread_mutex_t g_zmb_hid_lock = PTHREAD_MUTEX_INITIALIZER;

/*
 * Modern macOS can reject IOHIDPostEvent from a LoginWindow agent with
 * kIOReturnNotPrivileged even when the agent itself is running as root.  Keep
 * IOHID as the preferred low-level path, but fall back to Quartz's *session*
 * event tap.  kCGSessionEventTap is specifically the point where HID-system
 * and remote-control events enter a login session, which is a better fit than
 * kCGHIDEventTap for the pre-login agent.
 *
 * CGEventPost has no delivery result, so success here means that the event was
 * constructed and posted.  The real-Mac acceptance test remains authoritative.
 */
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
    if (g_zmb_hid == IO_OBJECT_NULL) {
        pthread_mutex_unlock(&g_zmb_hid_lock);
        return (int32_t)kIOReturnNotOpen;
    }
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    kern_return_t kr = IOHIDPostEvent(
        g_zmb_hid,
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

int32_t zmb_login_input_open(void) {
    pthread_mutex_lock(&g_zmb_hid_lock);
    if (g_zmb_hid != IO_OBJECT_NULL) {
        g_zmb_hid_refs += 1;
        pthread_mutex_unlock(&g_zmb_hid_lock);
        return (int32_t)kIOReturnSuccess;
    }
    io_service_t service = IOServiceGetMatchingService(
        kIOMainPortDefault,
        IOServiceMatching(kIOHIDSystemClass)
    );
    if (service == IO_OBJECT_NULL) {
        pthread_mutex_unlock(&g_zmb_hid_lock);
        return (int32_t)kIOReturnNotFound;
    }
    io_connect_t connection = IO_OBJECT_NULL;
    kern_return_t kr = IOServiceOpen(
        service,
        mach_task_self(),
        kIOHIDParamConnectType,
        &connection
    );
    IOObjectRelease(service);
    if (kr != kIOReturnSuccess) {
        pthread_mutex_unlock(&g_zmb_hid_lock);
        return (int32_t)kr;
    }
    g_zmb_hid = connection;
    g_zmb_hid_refs = 1;
    pthread_mutex_unlock(&g_zmb_hid_lock);
    return (int32_t)kIOReturnSuccess;
}

void zmb_login_input_close(void) {
    pthread_mutex_lock(&g_zmb_hid_lock);
    if (g_zmb_hid_refs > 0) {
        g_zmb_hid_refs -= 1;
    }
    if (g_zmb_hid != IO_OBJECT_NULL && g_zmb_hid_refs == 0) {
        IOServiceClose(g_zmb_hid);
        g_zmb_hid = IO_OBJECT_NULL;
    }
    pthread_mutex_unlock(&g_zmb_hid_lock);
}

int32_t zmb_login_input_key(uint16_t key_code, int32_t down,
                            uint32_t flags, int32_t modifier) {
    NXEventData data;
    memset(&data, 0, sizeof(data));
    data.key.repeat = 0;
    data.key.keyCode = key_code;
    data.key.origCharSet = NX_ASCIISET;
    data.key.charSet = NX_ASCIISET;
    UInt32 type = modifier ? NX_FLAGSCHANGED : (down ? NX_KEYDOWN : NX_KEYUP);
    IOOptionBits options = modifier ? kIOHIDSetGlobalEventFlags : 0;
    IOGPoint location = {0, 0};
    int32_t rc = zmb_post(type, location, &data, (IOOptionBits)flags, options);
    if ((kern_return_t)rc == kIOReturnNotPrivileged) {
        return zmb_quartz_key(key_code, down, flags);
    }
    return rc;
}

int32_t zmb_login_input_pointer(uint32_t type, int32_t x, int32_t y,
                                int32_t button, uint32_t flags) {
    NXEventData data;
    memset(&data, 0, sizeof(data));
    data.mouse.buttonNumber = (short)button;
    if (type == NX_LMOUSEDOWN || type == NX_RMOUSEDOWN || type == NX_OMOUSEDOWN ||
        type == NX_LMOUSEDRAGGED || type == NX_RMOUSEDRAGGED || type == NX_OMOUSEDRAGGED) {
        data.mouse.pressure = 255;
    }
    IOGPoint location = {(SInt32)x, (SInt32)y};
    int32_t rc = zmb_post(type, location, &data, (IOOptionBits)flags, kIOHIDSetCursorPosition);
    if ((kern_return_t)rc == kIOReturnNotPrivileged) {
        return zmb_quartz_pointer(type, x, y, button, flags);
    }
    return rc;
}

int32_t zmb_login_input_scroll(int32_t dx, int32_t dy, uint32_t flags) {
    NXEventData data;
    memset(&data, 0, sizeof(data));
    data.scrollWheel.deltaAxis1 = dy;
    data.scrollWheel.deltaAxis2 = dx;
    data.scrollWheel.pointDeltaAxis1 = dy;
    data.scrollWheel.pointDeltaAxis2 = dx;
    IOGPoint location = {0, 0};
    int32_t rc = zmb_post(NX_SCROLLWHEELMOVED, location, &data, (IOOptionBits)flags, 0);
    if ((kern_return_t)rc == kIOReturnNotPrivileged) {
        return zmb_quartz_scroll(dx, dy, flags);
    }
    return rc;
}
