import Foundation
import AppKit
import ScreenCaptureKit
import VideoToolbox
import CoreMedia
import CoreVideo

@available(macOS 13.0, *)
final class H264ScreenStreamer: NSObject, SCStreamOutput, SCStreamDelegate {
    private let fps: Int32
    private let maxWidth: Int
    private let bitrate: Int
    private let output: FileHandle
    private let logOutput: FileHandle
    private let sampleQueue = DispatchQueue(label: "com.ilovemyprojects.zorinmacbridge.capture", qos: .userInteractive)
    private let writeQueue = DispatchQueue(label: "com.ilovemyprojects.zorinmacbridge.streamwrite", qos: .userInteractive)
    private let done = DispatchSemaphore(value: 0)
    private let stateLock = NSLock()
    private var stream: SCStream?
    private var compression: VTCompressionSession?
    private var finished = false
    private var resultCode: Int32 = 0

    init(fps: Int32, maxWidth: Int, bitrate: Int, outputFD: Int32, logFD: Int32) {
        self.fps = max(1, min(fps, 60))
        self.maxWidth = max(640, maxWidth)
        self.bitrate = max(1_000_000, bitrate)
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
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else {
            throw NSError(domain: "ZorinMacBridge", code: 1, userInfo: [NSLocalizedDescriptionKey: "No display available for ScreenCaptureKit"])
        }

        let scale = min(1.0, Double(maxWidth) / Double(max(display.width, 1)))
        let width = max(2, Int((Double(display.width) * scale).rounded(.down)) & ~1)
        let height = max(2, Int((Double(display.height) * scale).rounded(.down)) & ~1)

        try createEncoder(width: Int32(width), height: Int32(height))

        let filter = SCContentFilter(display: display, excludingWindows: [])
        let config = SCStreamConfiguration()
        config.width = width
        config.height = height
        config.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(fps))
        config.queueDepth = 3
        config.showsCursor = true
        config.pixelFormat = kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange
        config.capturesAudio = false

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
        let keyInterval = max(1, fps * 2)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_MaxKeyFrameInterval, value: NSNumber(value: keyInterval))
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
        writeQueue.async { [weak self] in
            guard let self else { return }
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

@_cdecl("zmb_streamer_create")
public func zmb_streamer_create(_ fps: Int32, _ maxWidth: Int32, _ bitrate: Int32, _ outputFD: Int32, _ logFD: Int32) -> UnsafeMutableRawPointer? {
    guard #available(macOS 13.0, *) else { return nil }
    let streamer = H264ScreenStreamer(
        fps: fps,
        maxWidth: Int(maxWidth),
        bitrate: Int(bitrate),
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
