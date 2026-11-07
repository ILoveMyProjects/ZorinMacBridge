#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/hidsystem/IOHIDLib.h>
#include <IOKit/hidsystem/IOHIDParameter.h>
#include <IOKit/hidsystem/IOLLEvent.h>
#include <IOKit/hidsystem/ev_keymap.h>
#include <mach/mach.h>
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

static int32_t zmb_post(UInt32 type, IOGPoint location, NXEventData *data,
                        IOOptionBits flags, IOOptionBits options) {
    if (g_zmb_hid == IO_OBJECT_NULL) {
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
    return (int32_t)kr;
}

int32_t zmb_login_input_open(void) {
    if (g_zmb_hid != IO_OBJECT_NULL) {
        return (int32_t)kIOReturnSuccess;
    }
    io_service_t service = IOServiceGetMatchingService(
        kIOMainPortDefault,
        IOServiceMatching(kIOHIDSystemClass)
    );
    if (service == IO_OBJECT_NULL) {
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
        return (int32_t)kr;
    }
    g_zmb_hid = connection;
    return (int32_t)kIOReturnSuccess;
}

void zmb_login_input_close(void) {
    if (g_zmb_hid != IO_OBJECT_NULL) {
        IOServiceClose(g_zmb_hid);
        g_zmb_hid = IO_OBJECT_NULL;
    }
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
    return zmb_post(type, location, &data, (IOOptionBits)flags, options);
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
    return zmb_post(type, location, &data, (IOOptionBits)flags, kIOHIDSetCursorPosition);
}

int32_t zmb_login_input_scroll(int32_t dx, int32_t dy, uint32_t flags) {
    NXEventData data;
    memset(&data, 0, sizeof(data));
    data.scrollWheel.deltaAxis1 = dy;
    data.scrollWheel.deltaAxis2 = dx;
    data.scrollWheel.pointDeltaAxis1 = dy;
    data.scrollWheel.pointDeltaAxis2 = dx;
    IOGPoint location = {0, 0};
    return zmb_post(NX_SCROLLWHEELMOVED, location, &data, (IOOptionBits)flags, 0);
}
