import Foundation
import CoreGraphics
import ScreenCaptureKit
import CoreImage
import CoreMedia
import CoreVideo
import ImageIO
import Security
import CryptoKit

@_silgen_name("zmb_login_input_open")
private func zmb_login_input_open() -> Int32
@_silgen_name("zmb_login_input_close")
private func zmb_login_input_close()
@_silgen_name("zmb_login_input_key")
private func zmb_login_input_key(_ keyCode: UInt16, _ down: Int32, _ flags: UInt32, _ modifier: Int32) -> Int32
@_silgen_name("zmb_corehid_input_open")
private func zmb_corehid_input_open() -> Int32
@_silgen_name("zmb_corehid_input_close")
private func zmb_corehid_input_close()
@_silgen_name("zmb_corehid_input_key")
private func zmb_corehid_input_key(_ usage: UInt16, _ down: Int32) -> Int32
@_silgen_name("zmb_corehid_input_pointer")
private func zmb_corehid_input_pointer(_ x: Int32, _ y: Int32, _ buttons: UInt8, _ wheel: Int32) -> Int32

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

private final class ProbeStreamReceiver: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private let frameSemaphore = DispatchSemaphore(value: 0)
    private var imageBuffer: CVPixelBuffer?
    private var streamError: Error?
    private var completed = false

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of outputType: SCStreamOutputType) {
        guard outputType == .screen, sampleBuffer.isValid else { return }
        if let attachmentsArray = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
           let attachments = attachmentsArray.first,
           let raw = attachments[.status] as? Int,
           let status = SCFrameStatus(rawValue: raw),
           status != .complete && status != .started {
            return
        }
        guard let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        lock.lock()
        if !completed {
            completed = true
            imageBuffer = buffer
            lock.unlock()
            frameSemaphore.signal()
            return
        }
        lock.unlock()
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        lock.lock()
        if !completed {
            completed = true
            streamError = error
            lock.unlock()
            frameSemaphore.signal()
            return
        }
        lock.unlock()
    }

    func wait(timeout: TimeInterval) throws -> CVPixelBuffer {
        guard frameSemaphore.wait(timeout: .now() + timeout) == .success else {
            throw ProbeError.timeout("SCStream first frame")
        }
        lock.lock()
        defer { lock.unlock() }
        if let streamError { throw streamError }
        guard let imageBuffer else {
            throw NSError(domain: "ZorinMacBridgeCapabilityProbe", code: 14, userInfo: [NSLocalizedDescriptionKey: "SCStream callback returned no image buffer"])
        }
        return imageBuffer
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

private func waitForStreamFrame(filter: SCContentFilter, config: SCStreamConfiguration, timeout: TimeInterval) throws -> CGImage {
    let receiver = ProbeStreamReceiver()
    let queue = DispatchQueue(label: "com.ilovemyprojects.zorinmacbridge.probe.capture")
    let stream = SCStream(filter: filter, configuration: config, delegate: receiver)
    try stream.addStreamOutput(receiver, type: .screen, sampleHandlerQueue: queue)

    let startSemaphore = DispatchSemaphore(value: 0)
    let startBox = ProbeResultBox<Bool>()
    stream.startCapture { error in
        startBox.finish(value: error == nil, error: error)
        startSemaphore.signal()
    }
    guard startSemaphore.wait(timeout: .now() + 4.0) == .success else {
        stream.stopCapture(completionHandler: nil)
        throw ProbeError.timeout("SCStream.startCapture")
    }
    let (_, startError) = startBox.snapshot()
    if let startError { throw startError }

    defer { stream.stopCapture(completionHandler: nil) }
    let buffer = try receiver.wait(timeout: timeout)
    let ciImage = CIImage(cvPixelBuffer: buffer)
    let context = CIContext(options: nil)
    guard let image = context.createCGImage(ciImage, from: ciImage.extent) else {
        throw NSError(domain: "ZorinMacBridgeCapabilityProbe", code: 15, userInfo: [NSLocalizedDescriptionKey: "could not convert SCStream frame to CGImage"])
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
        for offset in stride(from: 0, to: raw.count, by: 4) {
            let r = Int(raw[offset])
            let g = Int(raw[offset + 1])
            let b = Int(raw[offset + 2])
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
        config.minimumFrameInterval = CMTime(value: 1, timescale: 30)
        config.queueDepth = 2
        config.capturesAudio = false
        let image = try waitForStreamFrame(filter: filter, config: config, timeout: 5.0)
        report.add("capture_scstream_first_frame", "PASS")
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

private func coreHIDAxis(_ value: CGFloat, origin: CGFloat, length: CGFloat) -> Int32 {
    guard length > 1 else { return 16384 }
    let normalized = min(1.0, max(0.0, Double((value - origin) / length)))
    return Int32((normalized * 32767.0).rounded())
}

private func runCoreHIDProbe(report: inout ProbeReport) -> Bool {
    let openRC = zmb_corehid_input_open()
    report.add("input_corehid_open_rc", Int(openRC))
    guard openRC == 0 else {
        report.add("input_corehid_result", "FAIL_OPEN")
        return false
    }
    defer { zmb_corehid_input_close() }

    let initialShift = CGEventSource.flagsState(.combinedSessionState).contains(.maskShift)
    guard !initialShift else {
        report.add("input_corehid_keyboard_result", "INCONCLUSIVE_SHIFT_ALREADY_ACTIVE")
        report.add("input_corehid_result", "INCONCLUSIVE")
        return false
    }

    let downRC = zmb_corehid_input_key(0xE1, 1)
    usleep(150_000)
    let downSeen = CGEventSource.flagsState(.combinedSessionState).contains(.maskShift)
    let upRC = zmb_corehid_input_key(0xE1, 0)
    usleep(150_000)
    let upSeen = !CGEventSource.flagsState(.combinedSessionState).contains(.maskShift)
    report.add("input_corehid_shift_down_rc", Int(downRC))
    report.add("input_corehid_shift_up_rc", Int(upRC))
    report.add("input_corehid_shift_down_seen", downSeen)
    report.add("input_corehid_shift_up_seen", upSeen)
    let keyboardOK = downRC == 0 && upRC == 0 && downSeen && upSeen
    report.add("input_corehid_keyboard_result", keyboardOK ? "PASS" : "FAIL")

    var pointerOK = false
    if let beforeEvent = CGEvent(source: nil) {
        let before = beforeEvent.location
        let bounds = CGDisplayBounds(CGMainDisplayID())
        if bounds.width > 40 && bounds.height > 40 {
            let localX = before.x - bounds.origin.x
            let targetLocalX = localX < bounds.width - 24 ? localX + 16 : max(8, localX - 16)
            let target = CGPoint(x: bounds.origin.x + targetLocalX, y: before.y)
            let targetX = coreHIDAxis(target.x, origin: bounds.origin.x, length: bounds.width)
            let targetY = coreHIDAxis(target.y, origin: bounds.origin.y, length: bounds.height)
            let pointerRC = zmb_corehid_input_pointer(targetX, targetY, 0, 0)
            usleep(180_000)
            let after = CGEvent(source: nil)?.location ?? before
            let moved = hypot(after.x - before.x, after.y - before.y) >= 2.0
            let restoreX = coreHIDAxis(before.x, origin: bounds.origin.x, length: bounds.width)
            let restoreY = coreHIDAxis(before.y, origin: bounds.origin.y, length: bounds.height)
            let restoreRC = zmb_corehid_input_pointer(restoreX, restoreY, 0, 0)
            usleep(120_000)
            report.add("input_corehid_pointer_rc", Int(pointerRC))
            report.add("input_corehid_pointer_restore_rc", Int(restoreRC))
            report.add("input_corehid_pointer_moved", moved)
            pointerOK = pointerRC == 0 && restoreRC == 0 && moved
        }
    }
    report.add("input_corehid_pointer_result", pointerOK ? "PASS" : "FAIL")

    let ok = keyboardOK && pointerOK
    report.add("input_corehid_result", ok ? "PASS" : "FAIL")
    report.add("input_corehid_detail", ok
        ? "production CoreHID keyboard and absolute pointer reports were observed by LoginWindow"
        : "production CoreHID keyboard and/or pointer proof failed")
    return ok
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
    // Stage 1 managed pre-login contract: legacy IOHID/Quartz remains diagnostic
    // only.  Production remote login is allowed to become READY only when the
    // Apple-managed CoreHID virtual-device path is proven on LoginWindow.
    report.add("input_result", coreHIDOK ? "PASS" : "FAIL")
    report.add("input_legacy_comparison", legacyOK ? "PASS" : "FAIL")

    if captureOK && coreHIDOK {
        report.add("overall", "PASS")
    } else if captureOK {
        report.add("overall", "CAPTURE_ONLY")
    } else if coreHIDOK {
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
