import Foundation
import AppKit
import ScreenCaptureKit
import VideoToolbox
import CoreMedia
import CoreVideo

private func writeStderr(_ text: String) {
    let line = (text + "\n").data(using: .utf8) ?? Data()
    try? FileHandle.standardError.write(contentsOf: line)
}

private func be32(_ value: UInt32) -> Data {
    var v = value.bigEndian
    return Data(bytes: &v, count: MemoryLayout<UInt32>.size)
}

@available(macOS 13.0, *)
final class H264ScreenStreamer: NSObject, SCStreamOutput, SCStreamDelegate {
    private let fps: Int32
    private let maxWidth: Int
    private let bitrate: Int
    private let output = FileHandle.standardOutput
    private let sampleQueue = DispatchQueue(label: "com.ilovemyprojects.zorinmacbridge.capture", qos: .userInteractive)
    private let writeQueue = DispatchQueue(label: "com.ilovemyprojects.zorinmacbridge.streamwrite", qos: .userInteractive)
    private var stream: SCStream?
    private var compression: VTCompressionSession?
    private var stopped = false

    init(fps: Int32, maxWidth: Int, bitrate: Int) {
        self.fps = max(1, min(fps, 60))
        self.maxWidth = max(640, maxWidth)
        self.bitrate = max(1_000_000, bitrate)
        super.init()
    }

    deinit {
        stop()
    }

    func start() async throws {
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

        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: sampleQueue)
        self.stream = stream
        try await stream.startCapture()
        writeStderr("stream-started width=\(width) height=\(height) fps=\(fps) bitrate=\(bitrate)")
    }

    func stop() {
        if stopped { return }
        stopped = true
        if let stream {
            stream.stopCapture(completionHandler: nil)
        }
        if let compression {
            VTCompressionSessionCompleteFrames(compression, untilPresentationTimeStamp: .invalid)
            VTCompressionSessionInvalidate(compression)
        }
        compression = nil
        stream = nil
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
        compression = session
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of outputType: SCStreamOutputType) {
        guard outputType == .screen, sampleBuffer.isValid, let compression else { return }

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
            compression,
            imageBuffer: imageBuffer,
            presentationTimeStamp: pts,
            duration: duration,
            frameProperties: nil,
            sourceFrameRefcon: nil,
            infoFlagsOut: nil
        )
        if status != noErr {
            writeStderr("encode-error status=\(status)")
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        writeStderr("stream-stopped error=\(error.localizedDescription)")
        exit(3)
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
            writeStderr("block-copy-error status=\(copyStatus)")
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
            do {
                try self.output.write(contentsOf: be32(UInt32(annexB.count)))
                try self.output.write(contentsOf: annexB)
            } catch {
                writeStderr("stdout-write-error=\(error.localizedDescription)")
                exit(4)
            }
        }
    }
}

private struct Options {
    var fps: Int32 = 30
    var maxWidth: Int = 2560
    var bitrate: Int = 8_000_000
}

private func parseOptions() -> Options {
    var opts = Options()
    let args = CommandLine.arguments
    var i = 1
    while i < args.count {
        switch args[i] {
        case "--fps" where i + 1 < args.count:
            opts.fps = Int32(args[i + 1]) ?? opts.fps
            i += 2
        case "--max-width" where i + 1 < args.count:
            opts.maxWidth = Int(args[i + 1]) ?? opts.maxWidth
            i += 2
        case "--bitrate" where i + 1 < args.count:
            opts.bitrate = Int(args[i + 1]) ?? opts.bitrate
            i += 2
        default:
            i += 1
        }
    }
    return opts
}

if #available(macOS 13.0, *) {
    let options = parseOptions()
    let streamer = H264ScreenStreamer(fps: options.fps, maxWidth: options.maxWidth, bitrate: options.bitrate)
    Task {
        do {
            try await streamer.start()
        } catch {
            writeStderr("fatal=\(error.localizedDescription)")
            exit(2)
        }
    }
    dispatchMain()
} else {
    writeStderr("fatal=macOS 13 or newer is required for ScreenCaptureKit streaming")
    exit(2)
}
