import Foundation
import CoreGraphics
import ScreenCaptureKit
import ImageIO
import Security
import CryptoKit
import CoreHID

@_silgen_name("zmb_login_input_open")
private func zmb_login_input_open() -> Int32
@_silgen_name("zmb_login_input_close")
private func zmb_login_input_close()
@_silgen_name("zmb_login_input_key")
private func zmb_login_input_key(_ keyCode: UInt16, _ down: Int32, _ flags: UInt32, _ modifier: Int32) -> Int32

private final class ProbeResultBox<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: T?
    private var error: Error?

    func finish(value: T?, error: Error?) {
        lock.lock()
        self.value = value
        self.error = error
        lock.unlock()
    }

    func snapshot() -> (T?, Error?) {
        lock.lock()
        defer { lock.unlock() }
        return (value, error)
    }
}

private final class ProbeHIDDelegate: HIDVirtualDeviceDelegate, @unchecked Sendable {
    func hidVirtualDevice(
        _ device: HIDVirtualDevice,
        receivedSetReportRequestOfType type: HIDReportType,
        id: HIDReportID?,
        data: Data
    ) async throws {
        // The capability probe does not expose writable output reports.
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

private struct ProbeReport {
    private(set) var rows: [(String, String)] = []

    mutating func add(_ key: String, _ value: String) {
        rows.append((key, sanitize(value)))
    }

    mutating func add(_ key: String, _ value: Int) {
        add(key, String(value))
    }

    mutating func add(_ key: String, _ value: Bool) {
        add(key, value ? "1" : "0")
    }

    func text() -> String {
        rows.map { "\($0.0)=\($0.1)" }.joined(separator: "\n") + "\n"
    }

    private func sanitize(_ value: String) -> String {
        value.replacingOccurrences(of: "\r", with: " ")
            .replacingOccurrences(of: "\n", with: " ")
            .prefix(1200)
            .description
    }
}

private enum ProbeError: Error, LocalizedError {
    case timeout(String)
    case noDisplay
    case screenshotUniform(Int)

    var errorDescription: String? {
        switch self {
        case .timeout(let stage): return "timeout while waiting for \(stage)"
        case .noDisplay: return "ScreenCaptureKit returned no displays"
        case .screenshotUniform(let range): return "captured image is black/uniform (luma range \(range))"
        }
    }
}

private func errorText(_ error: Error) -> String {
    let ns = error as NSError
    return "domain=\(ns.domain) code=\(ns.code) description=\(ns.localizedDescription)"
}

private func entitlementBool(_ key: String) -> Bool? {
    guard let task = SecTaskCreateFromSelf(nil) else { return nil }
    guard let raw = SecTaskCopyValueForEntitlement(task, key as CFString, nil) else { return false }
    if let number = raw as? NSNumber { return number.boolValue }
    return false
}

private func signingIdentifier() -> String {
    guard let task = SecTaskCreateFromSelf(nil) else { return "unknown" }
    return (SecTaskCopySigningIdentifier(task, nil) as String?) ?? "unknown"
}

private func waitForShareableContent(timeout: TimeInterval) throws -> SCShareableContent {
    let semaphore = DispatchSemaphore(value: 0)
    let box = ProbeResultBox<SCShareableContent>()
    SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: false) { content, error in
        box.finish(value: content, error: error)
        semaphore.signal()
    }
    guard semaphore.wait(timeout: .now() + timeout) == .success else {
        throw ProbeError.timeout("SCShareableContent")
    }
    let (content, error) = box.snapshot()
    if let error { throw error }
    guard let content else { throw ProbeError.noDisplay }
    return content
}

private func waitForScreenshot(filter: SCContentFilter, config: SCStreamConfiguration, timeout: TimeInterval) throws -> CGImage {
    let semaphore = DispatchSemaphore(value: 0)
    let box = ProbeResultBox<CGImage>()
    SCScreenshotManager.captureImage(contentFilter: filter, configuration: config) { image, error in
        box.finish(value: image, error: error)
        semaphore.signal()
    }
    guard semaphore.wait(timeout: .now() + timeout) == .success else {
        throw ProbeError.timeout("SCScreenshotManager.captureImage")
    }
    let (image, error) = box.snapshot()
    if let error { throw error }
    guard let image else {
        throw NSError(domain: "ZorinMacBridgeCapabilityProbe", code: 11, userInfo: [NSLocalizedDescriptionKey: "screenshot callback returned no CGImage"])
    }
    return image
}

private func imageLumaRange(_ image: CGImage) -> Int {
    let width = 64
    let height = 64
    let bytesPerRow = width * 4
    var bytes = [UInt8](repeating: 0, count: bytesPerRow * height)
    guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) else { return 0 }
    let result: Int = bytes.withUnsafeMutableBytes { raw in
        guard let base = raw.baseAddress,
              let ctx = CGContext(
                data: base,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: bytesPerRow,
                space: colorSpace,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
              ) else { return 0 }
        ctx.interpolationQuality = .low
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        var minLuma = 255
        var maxLuma = 0
        for offset in stride(from: 0, to: bytes.count, by: 4) {
            let r = Int(bytes[offset])
            let g = Int(bytes[offset + 1])
            let b = Int(bytes[offset + 2])
            let luma = (77 * r + 150 * g + 29 * b) >> 8
            minLuma = min(minLuma, luma)
            maxLuma = max(maxLuma, luma)
        }
        return maxLuma - minLuma
    }
    return result
}

private func writePNG(_ image: CGImage, to url: URL) throws -> (Int, String) {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    guard let destination = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil) else {
        throw NSError(domain: "ZorinMacBridgeCapabilityProbe", code: 12, userInfo: [NSLocalizedDescriptionKey: "could not create PNG destination"])
    }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else {
        throw NSError(domain: "ZorinMacBridgeCapabilityProbe", code: 13, userInfo: [NSLocalizedDescriptionKey: "could not finalize PNG"])
    }
    let data = try Data(contentsOf: url)
    let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    return (data.count, digest)
}

private func runCaptureProbe(report: inout ProbeReport, imageURL: URL) -> Bool {
    report.add("capture_preflight_screen_recording", CGPreflightScreenCaptureAccess())
    report.add("capture_remote_desktop_authorization_query", "UNAVAILABLE_PUBLIC_API")
    do {
        let content = try waitForShareableContent(timeout: 8.0)
        report.add("capture_shareable_content", "PASS")
        report.add("capture_display_count", content.displays.count)
        guard let display = content.displays.first else { throw ProbeError.noDisplay }
        report.add("capture_display_id", Int(display.displayID))
        report.add("capture_source_width", display.width)
        report.add("capture_source_height", display.height)

        let maxWidth = min(display.width, 1920)
        let scale = Double(maxWidth) / Double(max(display.width, 1))
        let width = max(2, Int((Double(display.width) * scale).rounded(.down)))
        let height = max(2, Int((Double(display.height) * scale).rounded(.down)))
        let config = SCStreamConfiguration()
        config.width = width
        config.height = height
        config.showsCursor = true
        config.pixelFormat = kCVPixelFormatType_32BGRA
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let image = try waitForScreenshot(filter: filter, config: config, timeout: 8.0)
        report.add("capture_screenshot", "PASS")
        report.add("capture_width", image.width)
        report.add("capture_height", image.height)
        let lumaRange = imageLumaRange(image)
        report.add("capture_luma_range", lumaRange)
        let (bytes, sha) = try writePNG(image, to: imageURL)
        report.add("capture_png_path", imageURL.path)
        report.add("capture_png_bytes", bytes)
        report.add("capture_png_sha256", sha)
        guard lumaRange >= 8 else {
            throw ProbeError.screenshotUniform(lumaRange)
        }
        report.add("capture_result", "PASS")
        return true
    } catch {
        report.add("capture_error", errorText(error))
        let ns = error as NSError
        if ns.domain == SCStreamError.errorDomain && ns.code == SCStreamError.Code.missingEntitlements.rawValue {
            report.add("capture_result", "BLOCKED_MISSING_ENTITLEMENT")
        } else if ns.domain == SCStreamError.errorDomain && ns.code == SCStreamError.Code.userDeclined.rawValue {
            report.add("capture_result", "BLOCKED_PERMISSION")
        } else if let probeError = error as? ProbeError, case .screenshotUniform = probeError {
            report.add("capture_result", "FAIL_BLACK_OR_UNIFORM")
        } else {
            report.add("capture_result", "FAIL")
        }
        return false
    }
}

private func coreHIDShiftProbe() async -> (String, String) {
    let descriptor = Data([
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
    let properties = HIDVirtualDevice.Properties(
        descriptor: descriptor,
        vendorID: 0x5A4D,
        productID: 0x0001,
        transport: nil,
        product: "ZMB Capability Probe Keyboard",
        manufacturer: "ZorinMacBridge",
        modelNumber: nil,
        versionNumber: 1,
        serialNumber: "stage-b",
        uniqueID: "zmb-stage-b-keyboard",
        locationID: nil,
        localizationCode: nil,
        extraProperties: nil
    )
    guard let device = HIDVirtualDevice(properties: properties) else {
        return ("FAIL_CREATE", "HIDVirtualDevice initializer returned nil")
    }
    let delegate = ProbeHIDDelegate()
    await device.activate(delegate: delegate)

    do {
        let clock = SuspendingClock()
        let before = CGEventSource.flagsState(.combinedSessionState).contains(.maskShift)
        guard !before else {
            return ("INCONCLUSIVE", "Shift modifier was already active before the probe")
        }

        try await device.dispatchInputReport(
            data: Data([0x02, 0, 0, 0, 0, 0, 0, 0]),
            timestamp: clock.now
        )
        try await Task.sleep(nanoseconds: 150_000_000)
        let downSeen = CGEventSource.flagsState(.combinedSessionState).contains(.maskShift)
        try await device.dispatchInputReport(
            data: Data(repeating: 0, count: 8),
            timestamp: clock.now
        )
        try await Task.sleep(nanoseconds: 150_000_000)
        let upSeen = !CGEventSource.flagsState(.combinedSessionState).contains(.maskShift)
        if downSeen && upSeen {
            return ("PASS", "virtual HID Shift changed combined session modifier state and released cleanly")
        }
        return ("FAIL_STATE", "virtual HID report dispatched but modifier state did not toggle as expected down=\(downSeen ? 1 : 0) up=\(upSeen ? 1 : 0)")
    } catch {
        return ("FAIL_DISPATCH", errorText(error))
    }
}

private func runCoreHIDProbe(report: inout ProbeReport) -> Bool {
    let semaphore = DispatchSemaphore(value: 0)
    let box = ProbeResultBox<(String, String)>()
    Task {
        let result = await coreHIDShiftProbe()
        box.finish(value: result, error: nil)
        semaphore.signal()
    }
    guard semaphore.wait(timeout: .now() + 8.0) == .success else {
        report.add("input_corehid_result", "TIMEOUT")
        return false
    }
    let (value, _) = box.snapshot()
    guard let value else {
        report.add("input_corehid_result", "FAIL_NO_RESULT")
        return false
    }
    report.add("input_corehid_result", value.0)
    report.add("input_corehid_detail", value.1)
    return value.0 == "PASS"
}

private func hexIOReturn(_ value: Int32) -> String {
    String(format: "0x%08x", UInt32(bitPattern: value))
}

private func runLegacyInputProbe(report: inout ProbeReport) -> Bool {
    let openRC = zmb_login_input_open()
    report.add("input_legacy_open_rc", hexIOReturn(openRC))
    guard openRC == 0 else {
        report.add("input_legacy_result", "FAIL_OPEN")
        return false
    }
    defer { zmb_login_input_close() }

    let initialShift = CGEventSource.flagsState(.combinedSessionState).contains(.maskShift)
    if initialShift {
        report.add("input_legacy_result", "INCONCLUSIVE_SHIFT_ALREADY_ACTIVE")
        return false
    }
    let shiftFlags = UInt32(truncatingIfNeeded: CGEventFlags.maskShift.rawValue)
    let downRC = zmb_login_input_key(56, 1, shiftFlags, 1)
    usleep(150_000)
    let downSeen = CGEventSource.flagsState(.combinedSessionState).contains(.maskShift)
    let upRC = zmb_login_input_key(56, 0, 0, 1)
    usleep(150_000)
    let upSeen = !CGEventSource.flagsState(.combinedSessionState).contains(.maskShift)
    report.add("input_legacy_shift_down_rc", hexIOReturn(downRC))
    report.add("input_legacy_shift_up_rc", hexIOReturn(upRC))
    report.add("input_legacy_shift_down_seen", downSeen)
    report.add("input_legacy_shift_up_seen", upSeen)
    if downRC == 0 && upRC == 0 && downSeen && upSeen {
        report.add("input_legacy_result", "PASS")
        return true
    }
    if downRC == 0 && upRC == 0 {
        report.add("input_legacy_result", "UNCONFIRMED_DELIVERY")
    } else {
        report.add("input_legacy_result", "FAIL")
    }
    return false
}

private func writeReport(_ report: ProbeReport, to url: URL) throws {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try Data(report.text().utf8).write(to: url, options: .atomic)
}

@_cdecl("zmb_capability_probe_run")
public func zmb_capability_probe_run(
    _ outputPath: UnsafePointer<CChar>?,
    _ imagePath: UnsafePointer<CChar>?
) -> Int32 {
    guard let outputPath, let imagePath else { return 64 }
    let outputURL = URL(fileURLWithPath: String(cString: outputPath))
    let imageURL = URL(fileURLWithPath: String(cString: imagePath))

    var report = ProbeReport()
    report.add("schema", "1")
    report.add("probe", "prelogin-capability")
    report.add("os_version", ProcessInfo.processInfo.operatingSystemVersionString)
    report.add("process_uid", Int(getuid()))
    report.add("process_euid", Int(geteuid()))
    report.add("signing_identifier", signingIdentifier())
    report.add("entitlement_persistent_content_capture", entitlementBool("com.apple.developer.persistent-content-capture") ?? false)
    report.add("entitlement_hid_virtual_device", entitlementBool("com.apple.developer.hid.virtual.device") ?? false)

    let captureOK = runCaptureProbe(report: &report, imageURL: imageURL)
    let coreHIDOK = runCoreHIDProbe(report: &report)
    let legacyOK = runLegacyInputProbe(report: &report)
    let inputOK = coreHIDOK || legacyOK
    report.add("input_result", inputOK ? "PASS" : "FAIL")

    if captureOK && inputOK {
        report.add("overall", "PASS")
    } else if captureOK {
        report.add("overall", "CAPTURE_ONLY")
    } else if inputOK {
        report.add("overall", "INPUT_ONLY")
    } else {
        let persistent = entitlementBool("com.apple.developer.persistent-content-capture") ?? false
        let hidEntitlement = entitlementBool("com.apple.developer.hid.virtual.device") ?? false
        report.add("overall", (!persistent || !hidEntitlement) ? "BLOCKED_OR_FAIL" : "FAIL")
    }

    do {
        try writeReport(report, to: outputURL)
        fputs(report.text(), stdout)
        fflush(stdout)
        return 0
    } catch {
        fputs("capability-probe-write-error=\(errorText(error))\n", stderr)
        fflush(stderr)
        return 74
    }
}
