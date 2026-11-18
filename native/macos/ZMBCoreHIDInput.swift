import Foundation
import CoreHID

private final class ZMBCoreHIDDelegate: HIDVirtualDeviceDelegate, @unchecked Sendable {
    // CoreHID's HIDVirtualDeviceDelegate was imported synchronously by the
    // Xcode 26 toolchain and asynchronously by newer SDKs. Keep both overloads
    // so the same source builds on the macOS-26 CI image and newer Xcode while
    // preserving one runtime behavior.
    func hidVirtualDevice(
        _ device: HIDVirtualDevice,
        receivedSetReportRequestOfType type: HIDReportType,
        id: HIDReportID?,
        data: Data
    ) throws {
        // ZorinMacBridge exposes input-only virtual HID devices.
    }

    func hidVirtualDevice(
        _ device: HIDVirtualDevice,
        receivedSetReportRequestOfType type: HIDReportType,
        id: HIDReportID?,
        data: Data
    ) async throws {
        // Same behavior for SDKs that import the delegate requirement async.
    }

    func hidVirtualDevice(
        _ device: HIDVirtualDevice,
        receivedGetReportRequestOfType type: HIDReportType,
        id: HIDReportID?,
        maxSize: Int
    ) throws -> Data {
        Data(repeating: 0, count: min(maxSize, 8))
    }

    func hidVirtualDevice(
        _ device: HIDVirtualDevice,
        receivedGetReportRequestOfType type: HIDReportType,
        id: HIDReportID?,
        maxSize: Int
    ) async throws -> Data {
        Data(repeating: 0, count: min(maxSize, 8))
    }
}

private final class ZMBInt32ResultBox: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Int32

    init(_ value: Int32) {
        self.value = value
    }

    func set(_ newValue: Int32) {
        lock.lock()
        value = newValue
        lock.unlock()
    }

    func get() -> Int32 {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}

private final class ZMBCoreHIDInputManager: @unchecked Sendable {
    static let shared = ZMBCoreHIDInputManager()

    private let lock = NSLock()
    private let delegate = ZMBCoreHIDDelegate()
    private var keyboard: HIDVirtualDevice?
    private var pointer: HIDVirtualDevice?
    private var modifierBits: UInt8 = 0
    private var pressedKeys: Set<UInt8> = []
    private var lastPointerX: UInt16 = 16384
    private var lastPointerY: UInt16 = 16384

    // USB HID boot-keyboard descriptor, identical to the capability-probe path.
    private let keyboardDescriptor = Data([
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
    ])

    // Three-button absolute pointer. Absolute X/Y avoids WindowServer cursor
    // queries from the LoginWindow helper. Wheel is signed relative motion.
    private let pointerDescriptor = Data([
        0x05, 0x01,       // Usage Page (Generic Desktop)
        0x09, 0x02,       // Usage (Mouse)
        0xA1, 0x01,       // Collection (Application)
        0x09, 0x01,       //   Usage (Pointer)
        0xA1, 0x00,       //   Collection (Physical)
        0x05, 0x09,       //     Usage Page (Button)
        0x19, 0x01,       //     Usage Minimum (Button 1)
        0x29, 0x03,       //     Usage Maximum (Button 3)
        0x15, 0x00,       //     Logical Minimum (0)
        0x25, 0x01,       //     Logical Maximum (1)
        0x95, 0x03,       //     Report Count (3)
        0x75, 0x01,       //     Report Size (1)
        0x81, 0x02,       //     Input (Data,Var,Abs)
        0x95, 0x01,       //     Report Count (1)
        0x75, 0x05,       //     Report Size (5)
        0x81, 0x01,       //     Input (Const)
        0x05, 0x01,       //     Usage Page (Generic Desktop)
        0x09, 0x30,       //     Usage (X)
        0x09, 0x31,       //     Usage (Y)
        0x16, 0x00, 0x00, //     Logical Minimum (0)
        0x26, 0xFF, 0x7F, //     Logical Maximum (32767)
        0x75, 0x10,       //     Report Size (16)
        0x95, 0x02,       //     Report Count (2)
        0x81, 0x02,       //     Input (Data,Var,Abs)
        0x09, 0x38,       //     Usage (Wheel)
        0x15, 0x81,       //     Logical Minimum (-127)
        0x25, 0x7F,       //     Logical Maximum (127)
        0x75, 0x08,       //     Report Size (8)
        0x95, 0x01,       //     Report Count (1)
        0x81, 0x06,       //     Input (Data,Var,Rel)
        0xC0,             //   End Collection
        0xC0              // End Collection
    ])

    private func makeDevice(descriptor: Data, product: String, uniqueID: String, productID: UInt32) -> HIDVirtualDevice? {
        HIDVirtualDevice(properties: HIDVirtualDevice.Properties(
            descriptor: descriptor,
            vendorID: 0x5A4D,
            productID: productID,
            transport: nil,
            product: product,
            manufacturer: "ZorinMacBridge",
            modelNumber: nil,
            versionNumber: 1,
            serialNumber: "prelogin",
            uniqueID: uniqueID,
            locationID: nil,
            localizationCode: nil,
            extraProperties: nil
        ))
    }

    private func activate(_ device: HIDVirtualDevice, timeout: TimeInterval = 3.0) -> Int32 {
        let semaphore = DispatchSemaphore(value: 0)
        Task {
            await device.activate(delegate: delegate)
            semaphore.signal()
        }
        return semaphore.wait(timeout: .now() + timeout) == .success ? 0 : -12
    }

    private func dispatch(_ data: Data, on device: HIDVirtualDevice, timeout: TimeInterval = 1.5) -> Int32 {
        let semaphore = DispatchSemaphore(value: 0)
        let result = ZMBInt32ResultBox(-14)
        Task {
            do {
                try await device.dispatchInputReport(data: data, timestamp: SuspendingClock.now)
                result.set(0)
            } catch {
                result.set(-14)
            }
            semaphore.signal()
        }
        guard semaphore.wait(timeout: .now() + timeout) == .success else { return -13 }
        return result.get()
    }

    func open() -> Int32 {
        lock.lock()
        if keyboard != nil, pointer != nil {
            lock.unlock()
            return 0
        }
        modifierBits = 0
        pressedKeys.removeAll()
        lastPointerX = 16384
        lastPointerY = 16384
        lock.unlock()

        guard let newKeyboard = makeDevice(
            descriptor: keyboardDescriptor,
            product: "ZorinMacBridge LoginWindow Keyboard",
            uniqueID: "zmb-loginwindow-keyboard",
            productID: 0x0001
        ), let newPointer = makeDevice(
            descriptor: pointerDescriptor,
            product: "ZorinMacBridge LoginWindow Pointer",
            uniqueID: "zmb-loginwindow-pointer",
            productID: 0x0002
        ) else {
            return -11
        }

        let keyboardRC = activate(newKeyboard)
        guard keyboardRC == 0 else { return keyboardRC }
        let pointerRC = activate(newPointer)
        guard pointerRC == 0 else { return pointerRC }

        lock.lock()
        keyboard = newKeyboard
        pointer = newPointer
        lock.unlock()
        return 0
    }

    func close() {
        lock.lock()
        let activeKeyboard = keyboard
        let activePointer = pointer
        let px = lastPointerX
        let py = lastPointerY
        keyboard = nil
        pointer = nil
        modifierBits = 0
        pressedKeys.removeAll()
        lock.unlock()

        // Release any remotely-held key/button before the virtual devices are
        // destroyed so a disconnected client cannot leave LoginWindow stuck.
        if let activeKeyboard {
            _ = dispatch(Data(repeating: 0, count: 8), on: activeKeyboard)
        }
        if let activePointer {
            let release = Data([
                0,
                UInt8(px & 0x00FF), UInt8((px >> 8) & 0x00FF),
                UInt8(py & 0x00FF), UInt8((py >> 8) & 0x00FF),
                0,
            ])
            _ = dispatch(release, on: activePointer)
        }
    }

    func key(usage: UInt16, down: Bool) -> Int32 {
        guard usage <= 0xFF else { return -16 }
        let u = UInt8(usage)

        lock.lock()
        guard let keyboard else {
            lock.unlock()
            return -10
        }
        if (0xE0...0xE7).contains(u) {
            let bit = UInt8(1 << Int(u - 0xE0))
            if down { modifierBits |= bit } else { modifierBits &= ~bit }
        } else if down {
            pressedKeys.insert(u)
        } else {
            pressedKeys.remove(u)
        }
        if pressedKeys.count > 6 {
            if down { pressedKeys.remove(u) }
            lock.unlock()
            return -15
        }
        let modifiers = modifierBits
        let keys = Array(pressedKeys.sorted().prefix(6))
        lock.unlock()

        var report = Data([modifiers, 0])
        report.append(contentsOf: keys)
        if keys.count < 6 {
            report.append(contentsOf: repeatElement(UInt8(0), count: 6 - keys.count))
        }
        return dispatch(report, on: keyboard)
    }

    func pointerReport(x: Int32, y: Int32, buttons: UInt8, wheel: Int32) -> Int32 {
        let cx = UInt16(max(0, min(32767, Int(x))))
        let cy = UInt16(max(0, min(32767, Int(y))))
        lock.lock()
        guard let pointer else {
            lock.unlock()
            return -10
        }
        lastPointerX = cx
        lastPointerY = cy
        lock.unlock()

        let cw = Int8(max(-127, min(127, Int(wheel))))
        let report = Data([
            buttons & 0x07,
            UInt8(cx & 0x00FF), UInt8((cx >> 8) & 0x00FF),
            UInt8(cy & 0x00FF), UInt8((cy >> 8) & 0x00FF),
            UInt8(bitPattern: cw),
        ])
        return dispatch(report, on: pointer)
    }
}

@_cdecl("zmb_corehid_input_open")
public func zmb_corehid_input_open() -> Int32 {
    ZMBCoreHIDInputManager.shared.open()
}

@_cdecl("zmb_corehid_input_close")
public func zmb_corehid_input_close() {
    ZMBCoreHIDInputManager.shared.close()
}

@_cdecl("zmb_corehid_input_key")
public func zmb_corehid_input_key(_ usage: UInt16, _ down: Int32) -> Int32 {
    ZMBCoreHIDInputManager.shared.key(usage: usage, down: down != 0)
}

@_cdecl("zmb_corehid_input_pointer")
public func zmb_corehid_input_pointer(_ x: Int32, _ y: Int32, _ buttons: UInt8, _ wheel: Int32) -> Int32 {
    ZMBCoreHIDInputManager.shared.pointerReport(x: x, y: y, buttons: buttons, wheel: wheel)
}
