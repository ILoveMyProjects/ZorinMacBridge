import Foundation
import AppKit
import ScreenCaptureKit
import VideoToolbox
import CoreMedia
import CoreVideo
import Darwin

// 2K/4K remote-desktop modes use a real macOS virtual display rather than
// enlarging/cropping the MacBook panel. CGVirtualDisplay is a private API, so
// resolve every class dynamically and fail closed if Apple removes it.
private final class VirtualDisplayManager {
    static let shared = VirtualDisplayManager()

    private let stateLock = NSLock()
    private var display: NSObject?
    private var width: Int = 0
    private var height: Int = 0
    private var _displayID: CGDirectDisplayID = 0
    private var originalOrigins: [CGDirectDisplayID: CGPoint] = [:]
    private var disabledBuiltInDisplayIDs: [CGDirectDisplayID] = []
    private var errorText: String = ""

    private typealias ConfigureDisplayEnabledFn = @convention(c) (CGDisplayConfigRef?, CGDirectDisplayID, Bool) -> CGError
    private lazy var configureDisplayEnabledFn: ConfigureDisplayEnabledFn? = {
        let paths = [
            "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight",
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics",
        ]
        for path in paths {
            guard let handle = dlopen(path, RTLD_LAZY | RTLD_LOCAL) else { continue }
            for name in ["SLSConfigureDisplayEnabled", "CGSConfigureDisplayEnabled"] {
                if let symbol = dlsym(handle, name) {
                    return unsafeBitCast(symbol, to: ConfigureDisplayEnabledFn.self)
                }
            }
        }
        return nil
    }()

    var displayID: CGDirectDisplayID {
        stateLock.lock(); defer { stateLock.unlock() }
        return _displayID
    }

    var lastError: String {
        stateLock.lock(); defer { stateLock.unlock() }
        return errorText
    }

    func ensure(width requestedWidth: Int, height requestedHeight: Int) -> CGDirectDisplayID {
        let w = max(640, min(requestedWidth, 7680))
        let h = max(480, min(requestedHeight, 4320))
        var result: CGDirectDisplayID = 0
        var failure = ""
        let action = {
            do {
                result = try self.ensureOnMain(width: w, height: h)
            } catch {
                failure = error.localizedDescription
            }
        }
        if Thread.isMainThread {
            action()
        } else {
            DispatchQueue.main.sync(execute: action)
        }
        stateLock.lock()
        errorText = failure
        stateLock.unlock()
        return result
    }

    func release() {
        let action = { self.releaseOnMain() }
        if Thread.isMainThread {
            action()
        } else {
            DispatchQueue.main.sync(execute: action)
        }
    }


    private func setDisplayEnabled(_ id: CGDirectDisplayID, enabled: Bool) -> Bool {
        guard let fn = configureDisplayEnabledFn else { return false }
        var config: CGDisplayConfigRef?
        guard CGBeginDisplayConfiguration(&config) == .success, let config else { return false }
        let result = fn(config, id, enabled)
        if result == .success {
            let complete = CGCompleteDisplayConfiguration(config, .forSession)
            return complete == .success
        }
        CGCancelDisplayConfiguration(config)
        return false
    }

    private func disableBuiltInDisplays(except virtualID: CGDirectDisplayID) throws {
        // A remote 2K/4K workspace must not leave windows/menu bar stranded on
        // the broken physical MacBook panel. Disable only built-in displays,
        // session-scoped, after the virtual display is confirmed online.
        if !disabledBuiltInDisplayIDs.isEmpty { return }
        var count: UInt32 = 0
        guard CGGetOnlineDisplayList(0, nil, &count) == .success else { return }
        var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
        guard CGGetOnlineDisplayList(count, &ids, &count) == .success else { return }
        let builtIns = ids.prefix(Int(count)).filter { $0 != virtualID && CGDisplayIsBuiltin($0) != 0 }
        guard !builtIns.isEmpty else { return }
        guard configureDisplayEnabledFn != nil else {
            throw NSError(domain: "ZorinMacBridge", code: 33, userInfo: [NSLocalizedDescriptionKey: "Headless virtual desktop requires CGS/SLSConfigureDisplayEnabled, which is unavailable on this macOS build"])
        }
        var disabled: [CGDirectDisplayID] = []
        for id in builtIns {
            if setDisplayEnabled(id, enabled: false) {
                disabled.append(id)
            } else {
                for restored in disabled { _ = setDisplayEnabled(restored, enabled: true) }
                throw NSError(domain: "ZorinMacBridge", code: 34, userInfo: [NSLocalizedDescriptionKey: "Could not detach the built-in MacBook display for the remote virtual desktop"])
            }
        }
        disabledBuiltInDisplayIDs = disabled
    }

    private func restoreBuiltInDisplays() {
        let ids = disabledBuiltInDisplayIDs
        disabledBuiltInDisplayIDs = []
        for id in ids { _ = setDisplayEnabled(id, enabled: true) }
    }

    private func onlineOrigins() -> [CGDirectDisplayID: CGPoint] {
        var count: UInt32 = 0
        guard CGGetOnlineDisplayList(0, nil, &count) == .success, count > 0 else { return [:] }
        var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
        guard CGGetOnlineDisplayList(count, &ids, &count) == .success else { return [:] }
        var out: [CGDirectDisplayID: CGPoint] = [:]
        for id in ids.prefix(Int(count)) {
            out[id] = CGDisplayBounds(id).origin
        }
        return out
    }

    private func restoreOrigins(_ origins: [CGDirectDisplayID: CGPoint]) {
        guard !origins.isEmpty else { return }
        var config: CGDisplayConfigRef?
        guard CGBeginDisplayConfiguration(&config) == .success, let config else { return }
        var ok = true
        for (id, point) in origins where CGDisplayIsOnline(id) != 0 {
            let result = CGConfigureDisplayOrigin(config, id, Int32(point.x.rounded()), Int32(point.y.rounded()))
            if result != .success { ok = false; break }
        }
        if ok {
            _ = CGCompleteDisplayConfiguration(config, .forSession)
        } else {
            CGCancelDisplayConfiguration(config)
        }
    }

    private func makePrimary(_ id: CGDirectDisplayID) {
        let bounds = CGDisplayBounds(id)
        let dx = -bounds.origin.x
        let dy = -bounds.origin.y
        if abs(dx) < 0.5 && abs(dy) < 0.5 { return }

        var count: UInt32 = 0
        guard CGGetOnlineDisplayList(0, nil, &count) == .success, count > 0 else { return }
        var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
        guard CGGetOnlineDisplayList(count, &ids, &count) == .success else { return }
        var config: CGDisplayConfigRef?
        guard CGBeginDisplayConfiguration(&config) == .success, let config else { return }
        var ok = true
        for displayID in ids.prefix(Int(count)) {
            // Mirror followers inherit their source's origin.
            if CGDisplayMirrorsDisplay(displayID) != kCGNullDirectDisplay { continue }
            let b = CGDisplayBounds(displayID)
            let x = Int32((b.origin.x + dx).rounded())
            let y = Int32((b.origin.y + dy).rounded())
            if CGConfigureDisplayOrigin(config, displayID, x, y) != .success {
                ok = false
                break
            }
        }
        if ok {
            _ = CGCompleteDisplayConfiguration(config, .forSession)
        } else {
            CGCancelDisplayConfiguration(config)
        }
    }

    private func ensureOnMain(width requestedWidth: Int, height requestedHeight: Int) throws -> CGDirectDisplayID {
        stateLock.lock()
        if let _ = display, width == requestedWidth, height == requestedHeight, _displayID != 0 {
            let id = _displayID
            stateLock.unlock()
            makePrimary(id)
            try disableBuiltInDisplays(except: id)
            return id
        }
        // Keep the working virtual display alive while constructing a replacement.
        // If 2K -> 4K creation fails, the old 2K desktop remains usable.
        var previousDisplay = display
        let previousOrigins = originalOrigins
        stateLock.unlock()

        let savedOrigins = previousOrigins.isEmpty ? onlineOrigins() : previousOrigins

        guard let DescriptorClass = NSClassFromString("CGVirtualDisplayDescriptor") as? NSObject.Type,
              let DisplayClass = NSClassFromString("CGVirtualDisplay") as? NSObject.Type,
              let ModeClass = NSClassFromString("CGVirtualDisplayMode") as? NSObject.Type,
              let SettingsClass = NSClassFromString("CGVirtualDisplaySettings") as? NSObject.Type else {
            throw NSError(domain: "ZorinMacBridge", code: 20, userInfo: [NSLocalizedDescriptionKey: "CGVirtualDisplay private API is unavailable on this macOS build"])
        }

        let descriptor = DescriptorClass.init()
        let descriptorSelectors = [
            "setVendorID:", "setProductID:", "setSerialNum:", "setName:",
            "setMaxPixelsWide:", "setMaxPixelsHigh:", "setSizeInMillimeters:", "setQueue:"
        ]
        guard descriptorSelectors.allSatisfy({ descriptor.responds(to: NSSelectorFromString($0)) }) else {
            throw NSError(domain: "ZorinMacBridge", code: 29, userInfo: [NSLocalizedDescriptionKey: "CGVirtualDisplayDescriptor API shape changed on this macOS build"])
        }
        descriptor.setValue(UInt32(0x5A4D), forKey: "vendorID")
        descriptor.setValue(UInt32(0x4247), forKey: "productID")
        descriptor.setValue(UInt32(truncatingIfNeeded: (requestedWidth << 16) ^ requestedHeight), forKey: "serialNum")
        descriptor.setValue("ZorinMacBridge Remote Display", forKey: "name")
        descriptor.setValue(UInt32(requestedWidth), forKey: "maxPixelsWide")
        descriptor.setValue(UInt32(requestedHeight), forKey: "maxPixelsHigh")
        let ppi = 110.0
        let physicalSize = CGSize(
            width: 25.4 * Double(requestedWidth) / ppi,
            height: 25.4 * Double(requestedHeight) / ppi
        )
        descriptor.setValue(NSValue(size: physicalSize), forKey: "sizeInMillimeters")
        descriptor.setValue(DispatchQueue(label: "com.ilovemyprojects.zorinmacbridge.virtual-display"), forKey: "queue")

        let allocSelector = NSSelectorFromString("alloc")
        guard let allocated = (DisplayClass as AnyObject).perform(allocSelector)?.takeRetainedValue() as? NSObject,
              let created = allocated.perform(NSSelectorFromString("initWithDescriptor:"), with: descriptor)?.takeUnretainedValue() as? NSObject else {
            throw NSError(domain: "ZorinMacBridge", code: 21, userInfo: [NSLocalizedDescriptionKey: "CGVirtualDisplay creation failed"])
        }

        let settings = SettingsClass.init()
        guard settings.responds(to: NSSelectorFromString("setHiDPI:")),
              settings.responds(to: NSSelectorFromString("setModes:")) else {
            throw NSError(domain: "ZorinMacBridge", code: 30, userInfo: [NSLocalizedDescriptionKey: "CGVirtualDisplaySettings API shape changed on this macOS build"])
        }
        settings.setValue(UInt32(0), forKey: "hiDPI")
        guard let modeAllocated = (ModeClass as AnyObject).perform(allocSelector)?.takeRetainedValue() as? NSObject else {
            throw NSError(domain: "ZorinMacBridge", code: 22, userInfo: [NSLocalizedDescriptionKey: "CGVirtualDisplayMode allocation failed"])
        }
        typealias ModeInitFunc = @convention(c) (AnyObject, Selector, UInt32, UInt32, Double) -> AnyObject?
        let modeSelector = NSSelectorFromString("initWithWidth:height:refreshRate:")
        guard modeAllocated.responds(to: modeSelector) else {
            throw NSError(domain: "ZorinMacBridge", code: 31, userInfo: [NSLocalizedDescriptionKey: "CGVirtualDisplayMode initializer changed on this macOS build"])
        }
        let modeInit = unsafeBitCast(modeAllocated.method(for: modeSelector), to: ModeInitFunc.self)
        guard let mode = modeInit(modeAllocated, modeSelector, UInt32(requestedWidth), UInt32(requestedHeight), 60.0) as? NSObject else {
            throw NSError(domain: "ZorinMacBridge", code: 23, userInfo: [NSLocalizedDescriptionKey: "CGVirtualDisplayMode initialization failed"])
        }
        settings.setValue([mode], forKey: "modes")

        typealias ApplySettingsFunc = @convention(c) (AnyObject, Selector, AnyObject) -> Bool
        let applySelector = NSSelectorFromString("applySettings:")
        guard created.responds(to: applySelector), created.responds(to: NSSelectorFromString("displayID")) else {
            throw NSError(domain: "ZorinMacBridge", code: 32, userInfo: [NSLocalizedDescriptionKey: "CGVirtualDisplay API shape changed on this macOS build"])
        }
        let applySettings = unsafeBitCast(created.method(for: applySelector), to: ApplySettingsFunc.self)
        guard applySettings(created, applySelector, settings) else {
            throw NSError(domain: "ZorinMacBridge", code: 24, userInfo: [NSLocalizedDescriptionKey: "CGVirtualDisplay settings were rejected"])
        }

        guard let rawID = created.value(forKey: "displayID") as? NSNumber else {
            throw NSError(domain: "ZorinMacBridge", code: 25, userInfo: [NSLocalizedDescriptionKey: "CGVirtualDisplay did not expose a displayID"])
        }
        let newID = CGDirectDisplayID(rawID.uint32Value)
        guard newID != 0 else {
            throw NSError(domain: "ZorinMacBridge", code: 26, userInfo: [NSLocalizedDescriptionKey: "CGVirtualDisplay returned an invalid displayID"])
        }

        // Give WindowServer a short bounded window to publish the new display.
        for _ in 0..<20 {
            if CGDisplayIsOnline(newID) != 0 && CGDisplayPixelsWide(newID) > 0 { break }
            Thread.sleep(forTimeInterval: 0.05)
        }
        guard CGDisplayIsOnline(newID) != 0 else {
            throw NSError(domain: "ZorinMacBridge", code: 27, userInfo: [NSLocalizedDescriptionKey: "Virtual display did not become online"])
        }

        // Only after the replacement is genuinely online do we commit it.
        makePrimary(newID)
        try disableBuiltInDisplays(except: newID)

        stateLock.lock()
        display = created
        width = requestedWidth
        height = requestedHeight
        _displayID = newID
        originalOrigins = savedOrigins
        stateLock.unlock()

        // Dropping the previous strong reference now removes the old virtual
        // display. A failed replacement above leaves the previous display intact.
        previousDisplay = nil
        return newID
    }

    private func releaseOnMain() {
        // Re-enable the physical MacBook panel while the virtual display still
        // exists, so there is never a moment with zero active displays.
        restoreBuiltInDisplays()

        stateLock.lock()
        var oldDisplay = display
        let origins = originalOrigins
        display = nil
        width = 0
        height = 0
        _displayID = 0
        originalOrigins = [:]
        stateLock.unlock()

        oldDisplay = nil
        if !origins.isEmpty {
            Thread.sleep(forTimeInterval: 0.10)
            restoreOrigins(origins)
        }
    }
}

@available(macOS 13.0, *)
final class H264ScreenStreamer: NSObject, SCStreamOutput, SCStreamDelegate {
    private let fps: Int32
    private let maxWidth: Int
    private let bitrate: Int
    private let targetWidth: Int
    private let targetHeight: Int
    private let displayID: CGDirectDisplayID
    private let output: FileHandle
    private let logOutput: FileHandle
    private let sampleQueue = DispatchQueue(label: "com.ilovemyprojects.zorinmacbridge.capture", qos: .userInteractive)
    private let writeQueue = DispatchQueue(label: "com.ilovemyprojects.zorinmacbridge.streamwrite", qos: .userInitiated)
    private let done = DispatchSemaphore(value: 0)
    private let stateLock = NSLock()
    private let writerLock = NSLock()
    private var stream: SCStream?
    private var compression: VTCompressionSession?
    private var finished = false
    private var resultCode: Int32 = 0
    private var writePending = false
    private var writerDropped: UInt64 = 0

    init(fps: Int32, maxWidth: Int, bitrate: Int, targetWidth: Int, targetHeight: Int, displayID: CGDirectDisplayID, outputFD: Int32, logFD: Int32) {
        self.fps = max(1, min(fps, 60))
        self.maxWidth = max(640, maxWidth)
        self.bitrate = max(1_000_000, bitrate)
        self.targetWidth = max(0, targetWidth)
        self.targetHeight = max(0, targetHeight)
        self.displayID = displayID
        self.output = FileHandle(fileDescriptor: outputFD, closeOnDealloc: true)
        self.logOutput = FileHandle(fileDescriptor: logFD, closeOnDealloc: true)
        super.init()
    }

    deinit {
        requestStop(code: 0, reason: nil)
    }

    private func log(_ text: String) {
        let line = (text + "\n").data(using: .utf8) ?? Data()
        do {
            try logOutput.write(contentsOf: line)
        } catch {
            // Logging must never take the capture process down.
        }
    }

    func runBlocking() -> Int32 {
        Task {
            do {
                try await self.start()
            } catch {
                self.requestStop(code: 2, reason: "fatal=\(error.localizedDescription)")
            }
        }
        done.wait()
        // Drain queued writes before closing the pipe ends so the Python side
        // receives complete access units and then observes EOF deterministically.
        writeQueue.sync {}
        try? output.close()
        try? logOutput.close()
        return resultCode
    }

    func requestStop(code: Int32 = 0, reason: String? = nil) {
        stateLock.lock()
        if finished {
            stateLock.unlock()
            return
        }
        finished = true
        resultCode = code
        let activeStream = stream
        let activeCompression = compression
        stream = nil
        compression = nil
        stateLock.unlock()

        if let reason { log(reason) }
        if let activeStream {
            activeStream.stopCapture(completionHandler: nil)
        }
        if let activeCompression {
            VTCompressionSessionCompleteFrames(activeCompression, untilPresentationTimeStamp: .invalid)
            VTCompressionSessionInvalidate(activeCompression)
        }
        done.signal()
    }

    private func start() async throws {
        var content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard !content.displays.isEmpty else {
            throw NSError(domain: "ZorinMacBridge", code: 1, userInfo: [NSLocalizedDescriptionKey: "No display available for ScreenCaptureKit"])
        }

        let display: SCDisplay
        if displayID != 0 {
            // CoreGraphics can publish a newly-created virtual display before
            // ScreenCaptureKit refreshes SCShareableContent. Never fall back to
            // the MacBook panel for an explicit virtual-display request.
            var requested = content.displays.first(where: { $0.displayID == displayID })
            for _ in 0..<50 where requested == nil {
                try await Task.sleep(nanoseconds: 100_000_000)
                content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
                requested = content.displays.first(where: { $0.displayID == displayID })
            }
            guard let exact = requested else {
                throw NSError(domain: "ZorinMacBridge", code: 35, userInfo: [NSLocalizedDescriptionKey: "Requested display is not yet visible to ScreenCaptureKit"])
            }
            display = exact
        } else {
            let mainDisplayID = CGMainDisplayID()
            guard let automatic = content.displays.first(where: { $0.displayID == mainDisplayID }) ?? content.displays.first else {
                throw NSError(domain: "ZorinMacBridge", code: 1, userInfo: [NSLocalizedDescriptionKey: "No display available for ScreenCaptureKit"])
            }
            display = automatic
        }

        let filter = SCContentFilter(display: display, excludingWindows: [])
        let explicitTarget = targetWidth > 0 && targetHeight > 0
        let width: Int
        let height: Int
        let sourceRect: CGRect?
        if explicitTarget {
            // Explicit remote resolutions must come from a real virtual macOS
            // desktop of that exact size. Never silently upscale/crop the small
            // MacBook panel and pretend it is a 2K/4K desktop.
            width = max(2, targetWidth & ~1)
            height = max(2, targetHeight & ~1)
            guard Int(display.width) == width && Int(display.height) == height else {
                throw NSError(
                    domain: "ZorinMacBridge",
                    code: 28,
                    userInfo: [NSLocalizedDescriptionKey: "Requested virtual desktop \(width)x\(height) is not active"]
                )
            }
            sourceRect = nil
        } else {
            let scale = min(1.0, Double(maxWidth) / Double(max(display.width, 1)))
            width = max(2, Int((Double(display.width) * scale).rounded(.down)) & ~1)
            height = max(2, Int((Double(display.height) * scale).rounded(.down)) & ~1)
            sourceRect = nil
        }

        try createEncoder(width: Int32(width), height: Int32(height))

        let config = SCStreamConfiguration()
        config.width = width
        config.height = height
        if let sourceRect { config.sourceRect = sourceRect }
        config.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(fps))
        config.queueDepth = 2
        config.showsCursor = true
        config.pixelFormat = kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange
        config.capturesAudio = false

        log(
            "display-selected id=\(display.displayID) source=\(display.width)x\(display.height) " +
            "builtin=\(CGDisplayIsBuiltin(display.displayID) != 0 ? 1 : 0) requested=\(displayID) " +
            "target=\(width)x\(height) crop=\(sourceRect.map { NSStringFromRect($0) } ?? "full")"
        )

        let newStream = SCStream(filter: filter, configuration: config, delegate: self)
        try newStream.addStreamOutput(self, type: .screen, sampleHandlerQueue: sampleQueue)
        guard installStreamUnlessFinished(newStream) else { return }
        try await newStream.startCapture()
        log("stream-started width=\(width) height=\(height) fps=\(fps) bitrate=\(bitrate)")
    }

    private func installStreamUnlessFinished(_ newStream: SCStream) -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        if finished { return false }
        stream = newStream
        return true
    }

    private func createEncoder(width: Int32, height: Int32) throws {
        var session: VTCompressionSession?
        let status = VTCompressionSessionCreate(
            allocator: kCFAllocatorDefault,
            width: width,
            height: height,
            codecType: kCMVideoCodecType_H264,
            encoderSpecification: [kVTVideoEncoderSpecification_EnableHardwareAcceleratedVideoEncoder: true] as CFDictionary,
            imageBufferAttributes: nil,
            compressedDataAllocator: nil,
            outputCallback: { refcon, _, status, infoFlags, sampleBuffer in
                guard status == noErr,
                      !infoFlags.contains(.frameDropped),
                      let refcon,
                      let sampleBuffer else { return }
                let owner = Unmanaged<H264ScreenStreamer>.fromOpaque(refcon).takeUnretainedValue()
                owner.handleEncoded(sampleBuffer)
            },
            refcon: Unmanaged.passUnretained(self).toOpaque(),
            compressionSessionOut: &session
        )
        guard status == noErr, let session else {
            throw NSError(domain: "ZorinMacBridge", code: Int(status), userInfo: [NSLocalizedDescriptionKey: "VTCompressionSessionCreate failed: \(status)"])
        }

        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_RealTime, value: kCFBooleanTrue)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_AllowFrameReordering, value: kCFBooleanFalse)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_ProfileLevel, value: kVTProfileLevel_H264_Main_AutoLevel)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_ExpectedFrameRate, value: NSNumber(value: fps))
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_AverageBitRate, value: NSNumber(value: bitrate))
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_MaxFrameDelayCount, value: NSNumber(value: 1))
        let dataRateLimits = [NSNumber(value: max(1, bitrate / 8)), NSNumber(value: 1)] as CFArray
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_DataRateLimits, value: dataRateLimits)
        let keyInterval = max(1, fps * 2)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_MaxKeyFrameInterval, value: NSNumber(value: keyInterval))
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration, value: NSNumber(value: 2.0))
        let prep = VTCompressionSessionPrepareToEncodeFrames(session)
        guard prep == noErr else {
            VTCompressionSessionInvalidate(session)
            throw NSError(domain: "ZorinMacBridge", code: Int(prep), userInfo: [NSLocalizedDescriptionKey: "VTCompressionSessionPrepareToEncodeFrames failed: \(prep)"])
        }
        stateLock.lock()
        compression = session
        stateLock.unlock()
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of outputType: SCStreamOutputType) {
        guard outputType == .screen, sampleBuffer.isValid else { return }
        stateLock.lock()
        let activeCompression = compression
        let isFinished = finished
        stateLock.unlock()
        guard !isFinished, let activeCompression else { return }

        if let attachmentsArray = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
           let attachments = attachmentsArray.first,
           let raw = attachments[.status] as? Int,
           let status = SCFrameStatus(rawValue: raw),
           status != .complete {
            return
        }

        guard let imageBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        let duration = CMTime(value: 1, timescale: CMTimeScale(fps))
        let status = VTCompressionSessionEncodeFrame(
            activeCompression,
            imageBuffer: imageBuffer,
            presentationTimeStamp: pts,
            duration: duration,
            frameProperties: nil,
            sourceFrameRefcon: nil,
            infoFlagsOut: nil
        )
        if status != noErr {
            log("encode-error status=\(status)")
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        requestStop(code: 3, reason: "stream-stopped error=\(error.localizedDescription)")
    }

    private func handleEncoded(_ sampleBuffer: CMSampleBuffer) {
        guard CMSampleBufferDataIsReady(sampleBuffer),
              let dataBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }

        var annexB = Data()
        let attachments = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false) as? [[CFString: Any]]
        let notSync = attachments?.first?[kCMSampleAttachmentKey_NotSync] as? Bool ?? false
        let isKeyframe = !notSync

        var nalHeaderLength: Int32 = 4
        if isKeyframe, let format = CMSampleBufferGetFormatDescription(sampleBuffer) {
            var parameterSetCount = 0
            var headerLength: Int32 = 0
            for index in 0..<2 {
                var pointer: UnsafePointer<UInt8>?
                var size = 0
                let status = CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
                    format,
                    parameterSetIndex: index,
                    parameterSetPointerOut: &pointer,
                    parameterSetSizeOut: &size,
                    parameterSetCountOut: &parameterSetCount,
                    nalUnitHeaderLengthOut: &headerLength
                )
                if status == noErr, let pointer, size > 0 {
                    annexB.append(contentsOf: [0, 0, 0, 1])
                    annexB.append(pointer, count: size)
                    nalHeaderLength = headerLength
                }
            }
        } else if let format = CMSampleBufferGetFormatDescription(sampleBuffer) {
            var pointer: UnsafePointer<UInt8>?
            var size = 0
            var count = 0
            var headerLength: Int32 = 0
            if CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
                format,
                parameterSetIndex: 0,
                parameterSetPointerOut: &pointer,
                parameterSetSizeOut: &size,
                parameterSetCountOut: &count,
                nalUnitHeaderLengthOut: &headerLength
            ) == noErr {
                nalHeaderLength = headerLength
            }
        }

        let total = CMBlockBufferGetDataLength(dataBuffer)
        var bytes = Data(count: total)
        let copyStatus = bytes.withUnsafeMutableBytes { rawBuffer in
            guard let base = rawBuffer.baseAddress else { return kCMBlockBufferBadLengthParameterErr }
            return CMBlockBufferCopyDataBytes(dataBuffer, atOffset: 0, dataLength: total, destination: base)
        }
        guard copyStatus == kCMBlockBufferNoErr else {
            log("block-copy-error status=\(copyStatus)")
            return
        }

        let headerSize = max(1, Int(nalHeaderLength))
        bytes.withUnsafeBytes { raw in
            guard let base = raw.bindMemory(to: UInt8.self).baseAddress else { return }
            var offset = 0
            while offset + headerSize <= total {
                var naluLength = 0
                for i in 0..<headerSize {
                    naluLength = (naluLength << 8) | Int(base[offset + i])
                }
                offset += headerSize
                if naluLength <= 0 || offset + naluLength > total { break }
                annexB.append(contentsOf: [0, 0, 0, 1])
                annexB.append(base.advanced(by: offset), count: naluLength)
                offset += naluLength
            }
        }

        guard !annexB.isEmpty, annexB.count <= 16 * 1024 * 1024 else { return }

        // Never accumulate an unbounded queue of old frames behind a slow pipe.
        // Remote desktop latency matters more than preserving every frame: when
        // one encoded access unit is already waiting/writing, drop the new one.
        writerLock.lock()
        if writePending {
            writerDropped &+= 1
            let dropped = writerDropped
            writerLock.unlock()
            if dropped == 1 || dropped % 120 == 0 {
                log("writer-drop count=\(dropped)")
            }
            return
        }
        writePending = true
        writerLock.unlock()

        writeQueue.async { [weak self] in
            guard let self else { return }
            defer {
                self.writerLock.lock()
                self.writePending = false
                self.writerLock.unlock()
            }
            self.stateLock.lock()
            let isFinished = self.finished
            self.stateLock.unlock()
            if isFinished { return }
            do {
                var length = UInt32(annexB.count).bigEndian
                let lengthData = Data(bytes: &length, count: MemoryLayout<UInt32>.size)
                try self.output.write(contentsOf: lengthData)
                try self.output.write(contentsOf: annexB)
            } catch {
                self.requestStop(code: 4, reason: "pipe-write-error=\(error.localizedDescription)")
            }
        }
    }
}

private func takeStreamer(_ handle: UnsafeMutableRawPointer) -> H264ScreenStreamer {
    return Unmanaged<H264ScreenStreamer>.fromOpaque(handle).takeUnretainedValue()
}

private func ownedCString(_ text: String) -> UnsafeMutablePointer<CChar>? {
    let bytes = text.utf8CString
    let pointer = UnsafeMutablePointer<CChar>.allocate(capacity: bytes.count)
    bytes.withUnsafeBufferPointer { source in
        if let base = source.baseAddress {
            _ = pointer.initialize(from: base, count: bytes.count)
        }
    }
    return pointer
}

@_cdecl("zmb_virtual_display_ensure")
public func zmb_virtual_display_ensure(_ width: Int32, _ height: Int32) -> UInt32 {
    let id = VirtualDisplayManager.shared.ensure(width: Int(width), height: Int(height))
    return UInt32(id)
}

@_cdecl("zmb_virtual_display_release")
public func zmb_virtual_display_release() {
    VirtualDisplayManager.shared.release()
}

@_cdecl("zmb_virtual_display_current_id")
public func zmb_virtual_display_current_id() -> UInt32 {
    return UInt32(VirtualDisplayManager.shared.displayID)
}

@_cdecl("zmb_virtual_display_last_error")
public func zmb_virtual_display_last_error() -> UnsafeMutablePointer<CChar>? {
    return ownedCString(VirtualDisplayManager.shared.lastError)
}

@_cdecl("zmb_display_list_json")
public func zmb_display_list_json() -> UnsafeMutablePointer<CChar>? {
    guard #available(macOS 13.0, *) else { return ownedCString("[]") }
    var rows: [[String: Any]] = []
    var count: UInt32 = 0
    guard CGGetOnlineDisplayList(0, nil, &count) == .success, count > 0 else {
        return ownedCString("[]")
    }
    var displayIDs = [CGDirectDisplayID](repeating: 0, count: Int(count))
    guard CGGetOnlineDisplayList(count, &displayIDs, &count) == .success else {
        return ownedCString("[]")
    }
    for displayID in displayIDs.prefix(Int(count)) {
        rows.append([
            "id": Int(displayID),
            "width": Int(CGDisplayPixelsWide(displayID)),
            "height": Int(CGDisplayPixelsHigh(displayID)),
            "builtin": CGDisplayIsBuiltin(displayID) != 0,
            "main": CGMainDisplayID() == displayID,
            "virtual": VirtualDisplayManager.shared.displayID == displayID,
        ])
    }
    guard JSONSerialization.isValidJSONObject(rows),
          let data = try? JSONSerialization.data(withJSONObject: rows, options: []),
          let json = String(data: data, encoding: .utf8) else {
        return ownedCString("[]")
    }
    return ownedCString(json)
}

@_cdecl("zmb_string_free")
public func zmb_string_free(_ pointer: UnsafeMutablePointer<CChar>?) {
    pointer?.deallocate()
}

@_cdecl("zmb_streamer_create")
public func zmb_streamer_create(_ fps: Int32, _ maxWidth: Int32, _ bitrate: Int32, _ targetWidth: Int32, _ targetHeight: Int32, _ displayID: UInt32, _ outputFD: Int32, _ logFD: Int32) -> UnsafeMutableRawPointer? {
    guard #available(macOS 13.0, *) else { return nil }
    let streamer = H264ScreenStreamer(
        fps: fps,
        maxWidth: Int(maxWidth),
        bitrate: Int(bitrate),
        targetWidth: Int(targetWidth),
        targetHeight: Int(targetHeight),
        displayID: CGDirectDisplayID(displayID),
        outputFD: outputFD,
        logFD: logFD
    )
    return Unmanaged.passRetained(streamer).toOpaque()
}

@_cdecl("zmb_streamer_run")
public func zmb_streamer_run(_ handle: UnsafeMutableRawPointer?) -> Int32 {
    guard let handle else { return 10 }
    if #available(macOS 13.0, *) {
        return takeStreamer(handle).runBlocking()
    }
    return 11
}

@_cdecl("zmb_streamer_stop")
public func zmb_streamer_stop(_ handle: UnsafeMutableRawPointer?) {
    guard let handle else { return }
    if #available(macOS 13.0, *) {
        takeStreamer(handle).requestStop(code: 0, reason: nil)
    }
}

@_cdecl("zmb_streamer_destroy")
public func zmb_streamer_destroy(_ handle: UnsafeMutableRawPointer?) {
    guard let handle else { return }
    Unmanaged<H264ScreenStreamer>.fromOpaque(handle).release()
}
